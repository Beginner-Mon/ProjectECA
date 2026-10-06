"""VvaMonitoringStack: the "vva-lambdas" dashboard (SCRUM-39) and the LLM judge alarm.

Pins what the stack's docstring promises:
- every VVA function gets its row (invocations/errors/throttles, duration,
  memory from the REPORT lines);
- the stack imports nothing from the other stacks, so it never blocks their
  deploys;
- it stays under 50 metrics, the limit for a free dashboard;
- 3 `grader_judge_failed` lines in an hour on vva-agent alert the topic.
"""

import json

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from infra.monitoring_stack import (
    ALERT_TOPIC_NAME,
    DASHBOARD_NAME,
    FUNCTION_NAMES,
    JUDGE_ALARM_NAME,
    MonitoringStack,
)


@pytest.fixture(scope="module")
def template() -> dict:
    app = cdk.App()
    stack = MonitoringStack(
        app, "VvaMonitoringStack",
        env=cdk.Environment(account="244203483654", region="us-east-1"),
    )
    return Template.from_stack(stack).to_json()


def _dashboard(template: dict) -> dict:
    dashboards = [r for r in template["Resources"].values()
                  if r["Type"] == "AWS::CloudWatch::Dashboard"]
    assert len(dashboards) == 1
    return dashboards[0]["Properties"]


def _widgets(template: dict) -> list[dict]:
    body = _dashboard(template)["DashboardBody"]
    if isinstance(body, dict):  # Fn::Join around region tokens
        body = "".join(p if isinstance(p, str) else "us-east-1" for p in body["Fn::Join"][1])
    return json.loads(body)["widgets"]


def test_dashboard_is_named(template):
    assert _dashboard(template)["DashboardName"] == DASHBOARD_NAME


@pytest.mark.parametrize("name", FUNCTION_NAMES)
def test_every_function_has_its_row(template, name):
    titles = [w["properties"].get("title", "") for w in _widgets(template)]
    assert f"{name}: invocations, errors, throttles" in titles
    assert f"{name}: duration (ms)" in titles
    assert f"{name}: memory used (MB)" in titles


@pytest.mark.parametrize("name", FUNCTION_NAMES)
def test_memory_widget_reads_the_function_log_group(template, name):
    queries = [w["properties"]["query"] for w in _widgets(template) if w["type"] == "log"]
    assert any(f"/aws/lambda/{name}'" in q and "REPORT" in q for q in queries)


def test_stack_imports_nothing_from_other_stacks(template):
    assert "Fn::ImportValue" not in json.dumps(template)


def test_dashboard_stays_under_50_metrics(template):
    graphs = [w for w in _widgets(template) if w["type"] == "metric"]
    metrics = sum(len(w["properties"].get("metrics", [])) for w in graphs)
    alarms = sum(len(w["properties"].get("annotations", {}).get("alarms", [])) for w in graphs)
    assert metrics == len(FUNCTION_NAMES) * 6
    assert alarms == 1
    assert metrics + alarms <= 50


def _resources(template: dict, type_: str) -> dict:
    return {k: r["Properties"] for k, r in template["Resources"].items() if r["Type"] == type_}


def test_judge_failures_are_counted_from_the_agent_logs(template):
    (f,) = _resources(template, "AWS::Logs::MetricFilter").values()
    assert f["LogGroupName"] == "/aws/lambda/vva-agent"
    assert f["FilterPattern"] == '"grader_judge_failed"'
    (t,) = f["MetricTransformations"]
    assert (t["MetricNamespace"], t["MetricName"], t["MetricValue"]) == (
        "VVA/Grader", "JudgeFailed", "1")


def test_three_judge_failures_in_an_hour_alert_the_topic(template):
    (alarm,) = _resources(template, "AWS::CloudWatch::Alarm").values()
    assert alarm["AlarmName"] == JUDGE_ALARM_NAME
    assert (alarm["Namespace"], alarm["MetricName"]) == ("VVA/Grader", "JudgeFailed")
    assert (alarm["Statistic"], alarm["Period"], alarm["EvaluationPeriods"]) == ("Sum", 3600, 1)
    assert alarm["Threshold"] == 3
    assert alarm["ComparisonOperator"] == "GreaterThanOrEqualToThreshold"
    assert alarm["TreatMissingData"] == "notBreaching"
    ((topic_id, topic),) = _resources(template, "AWS::SNS::Topic").items()
    assert topic["TopicName"] == ALERT_TOPIC_NAME
    assert alarm["AlarmActions"] == [{"Ref": topic_id}]


def test_topic_has_no_subscription_in_the_template(template):
    # Subscribed once by hand (see the stack docstring): an address in the
    # template would be in the repo, and a context flag could be forgotten.
    assert not _resources(template, "AWS::SNS::Subscription")
    assert "AlertTopicArn" in template["Outputs"]
    assert "Export" not in template["Outputs"]["AlertTopicArn"]


def test_judge_row_on_the_dashboard(template):
    widgets = _widgets(template)
    titles = [w["properties"].get("title", "") for w in widgets]
    assert "vva-agent: LLM judge failures per hour (alarm at 3)" in titles
    (query,) = [w["properties"]["query"] for w in widgets
                if w["properties"].get("title") == "vva-agent: LLM judge calls and failures"]
    assert "/aws/lambda/vva-agent'" in query and "grader_judge_failed" in query
