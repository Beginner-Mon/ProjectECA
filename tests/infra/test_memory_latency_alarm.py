"""Alarm that reopens the no-Redis decision (17/10): memory node p90 > 300 ms."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from infra.monitoring_stack import MEMORY_ALARM_NAME, MEMORY_P90_MS, MonitoringStack

_ENV = cdk.Environment(account="244203483654", region="us-east-1")


def _template() -> Template:
    return Template.from_stack(MonitoringStack(cdk.App(), "Mon", env=_ENV))


def test_memory_node_latency_becomes_a_metric():
    _template().has_resource_properties("AWS::Logs::MetricFilter", {
        "LogGroupName": "/aws/lambda/vva-agent",
        "FilterPattern": Match.string_like_regexp(r'\$\.msg = "node_complete".*\$\.node = "memory"'),
        "MetricTransformations": [Match.object_like({
            "MetricNamespace": "VVA/Memory",
            "MetricName": "NodeElapsedMs",
            "MetricValue": "$.elapsed_ms",
        })],
    })


def test_alarm_fires_on_p90_over_300ms_in_two_of_three_hours_and_notifies():
    assert MEMORY_P90_MS == 300
    _template().has_resource_properties("AWS::CloudWatch::Alarm", {
        "AlarmName": MEMORY_ALARM_NAME,
        "ExtendedStatistic": "p90",
        "Threshold": 300,
        "EvaluationPeriods": 3,
        "DatapointsToAlarm": 2,
        "Period": 3600,
        "ComparisonOperator": "GreaterThanThreshold",
        "TreatMissingData": "notBreaching",
        "AlarmActions": [Match.object_like({"Ref": Match.string_like_regexp("AlertTopic")})],
    })
