"""VvaMonitoringStack: the "vva-lambdas" dashboard (SCRUM-39).

Pins three things the stack's docstring promises:
- every VVA function gets its row (invocations/errors/throttles, duration,
  memory from the REPORT lines);
- the stack imports nothing from the other stacks, so it never blocks their
  deploys;
- it stays under 50 metrics, the limit for a free dashboard.
"""

import json

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from infra.monitoring_stack import DASHBOARD_NAME, FUNCTION_NAMES, MonitoringStack


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
    metrics = sum(len(w["properties"].get("metrics", [])) for w in _widgets(template)
                  if w["type"] == "metric")
    assert metrics == len(FUNCTION_NAMES) * 6
    assert metrics <= 50
