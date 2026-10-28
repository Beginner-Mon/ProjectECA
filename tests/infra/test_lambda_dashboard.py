"""VvaMonitoringStack: the "vva-lambdas" dashboard (SCRUM-39) and the guardrail alarms.

Pins what the stack's docstring promises:
- every VVA function gets its row (invocations/errors/throttles, duration,
  memory from the REPORT lines);
- the stack imports nothing from the other stacks, so it never blocks their
  deploys;
- it stays under 50 metrics, the limit for a free dashboard;
- 3 `guardrail_triage_failed` lines in an hour on vva-agent alert the topic;
- triage p90 above 1000 ms in 2 of the last 3 hours alerts the topic.
"""

import json

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from infra.monitoring_stack import (
    ALERT_TOPIC_NAME,
    DASHBOARD_NAME,
    FUNCTION_NAMES,
    TRIAGE_ALARM_NAME,
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
    assert alarms == 3          # triage failures + triage latency p90 + memory node latency
    assert metrics + alarms <= 50


def _resources(template: dict, type_: str) -> dict:
    return {k: r["Properties"] for k, r in template["Resources"].items() if r["Type"] == type_}


def test_triage_failures_are_counted_from_the_agent_logs(template):
    (f,) = [f for f in _resources(template, "AWS::Logs::MetricFilter").values()
            if f["MetricTransformations"][0]["MetricNamespace"] == "VVA/Guardrail"
            and f["MetricTransformations"][0]["MetricName"] == "TriageFailed"]
    assert f["LogGroupName"] == "/aws/lambda/vva-agent"
    assert f["FilterPattern"] == '"guardrail_triage_failed"'
    (t,) = f["MetricTransformations"]
    assert (t["MetricNamespace"], t["MetricName"], t["MetricValue"]) == (
        "VVA/Guardrail", "TriageFailed", "1")


def test_three_triage_failures_in_an_hour_alert_the_topic(template):
    (alarm,) = [a for a in _resources(template, "AWS::CloudWatch::Alarm").values()
                if a["AlarmName"] == TRIAGE_ALARM_NAME]
    assert (alarm["Namespace"], alarm["MetricName"]) == ("VVA/Guardrail", "TriageFailed")
    assert (alarm["Statistic"], alarm["Period"], alarm["EvaluationPeriods"]) == ("Sum", 3600, 1)
    assert alarm["Threshold"] == 3
    assert alarm["ComparisonOperator"] == "GreaterThanOrEqualToThreshold"
    assert alarm["TreatMissingData"] == "notBreaching"
    ((topic_id, topic),) = _resources(template, "AWS::SNS::Topic").items()
    assert topic["TopicName"] == ALERT_TOPIC_NAME
    assert alarm["AlarmActions"] == [{"Ref": topic_id}]


def test_triage_latency_is_extracted_from_triage_logs(template):
    (f,) = [f for f in _resources(template, "AWS::Logs::MetricFilter").values()
            if f["MetricTransformations"][0]["MetricNamespace"] == "VVA/Guardrail"
            and f["MetricTransformations"][0]["MetricName"] == "TriageElapsedMs"]
    assert f["LogGroupName"] == "/aws/lambda/vva-agent"
    assert '"guardrail_triage"' in f["FilterPattern"]
    (t,) = f["MetricTransformations"]
    assert (t["MetricNamespace"], t["MetricName"], t["MetricValue"]) == (
        "VVA/Guardrail", "TriageElapsedMs", "$.elapsed_ms")


def test_triage_p90_latency_above_threshold_alerts(template):
    (alarm,) = [a for a in _resources(template, "AWS::CloudWatch::Alarm").values()
                if a["AlarmName"] == "vva-guardrail-triage-latency-p90"]
    assert (alarm["Namespace"], alarm["MetricName"]) == ("VVA/Guardrail", "TriageElapsedMs")
    assert (alarm["Statistic"], alarm["Period"], alarm["EvaluationPeriods"], alarm["DatapointsToAlarm"]) == ("p90", 3600, 3, 2)
    assert alarm["Threshold"] == 1000
    assert alarm["ComparisonOperator"] == "GreaterThanThreshold"
    assert alarm["TreatMissingData"] == "notBreaching"
    ((topic_id, topic),) = _resources(template, "AWS::SNS::Topic").items()
    assert alarm["AlarmActions"] == [{"Ref": topic_id}]


def test_topic_has_no_subscription_in_the_template(template):
    # Subscribed once by hand (see the stack docstring): an address in the
    # template would be in the repo, and a context flag could be forgotten.
    assert not _resources(template, "AWS::SNS::Subscription")
    assert "AlertTopicArn" in template["Outputs"]
    assert "Export" not in template["Outputs"]["AlertTopicArn"]


def test_guardrail_rows_on_the_dashboard(template):
    widgets = _widgets(template)
    titles = [w["properties"].get("title", "") for w in widgets]
    assert "vva-agent: guardrail triage failures per hour (alarm at 3)" in titles
    assert "vva-agent: guardrail triage p90 latency (alarm at 1000 ms)" in titles
    (query,) = [w["properties"]["query"] for w in widgets
                if w["properties"].get("title") == "vva-agent: guardrail triage calls and failures"]
    assert "/aws/lambda/vva-agent'" in query and "guardrail_triage_failed" in query
