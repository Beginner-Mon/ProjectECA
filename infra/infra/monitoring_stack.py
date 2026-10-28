"""Monitoring Stack: one CloudWatch dashboard for the VVA Lambda functions (SCRUM-39).

    cdk deploy VvaMonitoringStack

Per function, one row:
    - invocations, errors and throttles (sums per 5 minutes)
    - duration p50 / p95 / max
    - memory used, read from the REPORT line of each invocation (Logs Insights)

Memory comes from the logs because Lambda publishes no memory metric without
Lambda Insights, and memory is what has bitten us: the agent ran out at 1024 MB
(R-T1, 14/09), and the SpeechLLm measured 2932 of its 3008 MB (R-T2).

Functions are named by string, not passed in as `fn` objects from the other
stacks. A reference to another stack's function becomes a CloudFormation
export, and an export cannot be removed while this stack imports it, so every
later change to those stacks would have to deploy this one first. A dashboard
only needs the name.

Cost: the account's first three dashboards are free and this one stays under
50 metrics. The Logs Insights widgets are billed per GB scanned, only when
someone opens the dashboard.

Guardrail triage alarm. When the triage call fails (timeout, provider error,
unparseable), the turn continues with a routine fallback verdict by design,
so a triage that is down for everyone looks the same as a healthy one from
the outside. Each failure logs `guardrail_triage_failed`; a metric filter on
the vva-agent log group counts them, and 3 in one hour sets off
`vva-guardrail-triage-failed`, which publishes to the `vva-alerts` topic.
One or two timeouts while the model is slow are expected and cost nothing but
the check; three in an hour means triage is down for whoever is using the app.

Guardrail triage latency alarm. Each successful triage call logs
`guardrail_triage` with `elapsed_ms`. A metric filter extracts this value;
a p90 alarm fires when the hourly p90 exceeds 1000 ms in 2 of the last 3 hours.

Nobody hears the alarm until someone subscribes, once, outside CDK. An email
address does not belong in this repo, and a context flag forgotten on a later
deploy would silently delete the subscription:

    aws sns subscribe --topic-arn <AlertTopicArn output> \
        --protocol email --notification-endpoint <address>

then click the confirmation link in the email.

Cost of the alarm: within the account's 10 free alarms; the metric is billed
(at most $0.30/month) only in hours when a failure is logged; email delivery is
free up to 1,000 a month.
"""

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from constructs import Construct

DASHBOARD_NAME = "vva-lambdas"
ALERT_TOPIC_NAME = "vva-alerts"
# Guardrail triage alarms (replaced grader judge alarm in Phase 7).
TRIAGE_ALARM_NAME = "vva-guardrail-triage-failed"
TRIAGE_FAILURES_PER_HOUR = 3
# Guardrail triage latency alarm — p90 threshold (ms).
TRIAGE_P90_MS = 1000
# No Redis (17/10): the memory node reads recent messages from Neon. p50 76 ms,
# p90 123 ms on prod (15/09–17/10). Past this, revisit: skip queries, pooler,
# then a cache — see docs/plans/reupdate-plan.md D35.
MEMORY_ALARM_NAME = "vva-memory-node-slow"
MEMORY_P90_MS = 300

# function_name values set in agent_stack.py, crud_api_stack.py,
# character_stack.py and speechllm_stack.py. The warmers are left out: they
# only keep the two main functions warm, and their failures show up as cold
# starts in the duration of those functions.
FUNCTION_NAMES = ("vva-agent", "vva-crud-api", "vva-characters", "vva-speechllm")

_PERIOD = Duration.minutes(5)


def _lambda_metric(function_name: str, metric_name: str, statistic: str, label: str) -> cw.Metric:
    return cw.Metric(
        namespace="AWS/Lambda",
        metric_name=metric_name,
        dimensions_map={"FunctionName": function_name},
        statistic=statistic,
        period=_PERIOD,
        label=label,
    )


class MonitoringStack(Stack):
    def __init__(self, scope: Construct, construct_id: str,
                 function_names: tuple[str, ...] = FUNCTION_NAMES, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.dashboard = cw.Dashboard(
            self, "LambdaDashboard",
            dashboard_name=DASHBOARD_NAME,
            default_interval=Duration.hours(24),
        )

        for name in function_names:
            self.dashboard.add_widgets(
                cw.GraphWidget(
                    title=f"{name}: invocations, errors, throttles",
                    left=[
                        _lambda_metric(name, "Invocations", "Sum", "Invocations"),
                        _lambda_metric(name, "Errors", "Sum", "Errors"),
                        _lambda_metric(name, "Throttles", "Sum", "Throttles"),
                    ],
                    width=8,
                ),
                cw.GraphWidget(
                    title=f"{name}: duration (ms)",
                    left=[
                        _lambda_metric(name, "Duration", "p50", "p50"),
                        _lambda_metric(name, "Duration", "p95", "p95"),
                        _lambda_metric(name, "Duration", "Maximum", "max"),
                    ],
                    width=8,
                ),
                cw.LogQueryWidget(
                    title=f"{name}: memory used (MB)",
                    log_group_names=[f"/aws/lambda/{name}"],
                    query_lines=[
                        'filter @type = "REPORT"',
                        "stats max(@maxMemoryUsed / 1000 / 1000) as max_mb,"
                        " avg(@maxMemoryUsed / 1000 / 1000) as avg_mb by bin(1h)",
                    ],
                    view=cw.LogQueryVisualizationType.LINE,
                    width=8,
                ),
            )

        self._guardrail_alarms()
        self._memory_alarm()

    def _guardrail_alarms(self) -> None:
        """Guardrail triage failure count + p90 latency → metric → alarm → topic."""
        agent_logs = "/aws/lambda/vva-agent"

        # --- TriageFailed: counts `guardrail_triage_failed` log lines ---
        logs.MetricFilter(
            self, "TriageFailedFilter",
            log_group=logs.LogGroup.from_log_group_name(self, "AgentLogs", agent_logs),
            filter_pattern=logs.FilterPattern.all_terms("guardrail_triage_failed"),
            metric_namespace="VVA/Guardrail",
            metric_name="TriageFailed",
            metric_value="1",
        )
        failures = cw.Metric(
            namespace="VVA/Guardrail",
            metric_name="TriageFailed",
            statistic="Sum",
            period=Duration.hours(1),
        )
        self.alert_topic = sns.Topic(self, "AlertTopic", topic_name=ALERT_TOPIC_NAME)
        alarm = cw.Alarm(
            self, "TriageFailedAlarm",
            alarm_name=TRIAGE_ALARM_NAME,
            alarm_description=(
                f"{TRIAGE_FAILURES_PER_HOUR}+ guardrail_triage_failed in one hour on vva-agent: "
                "turns are falling back to routine without triage. Logs: /aws/lambda/vva-agent, "
                "field `error` names the exception type."
            ),
            metric=failures,
            threshold=TRIAGE_FAILURES_PER_HOUR,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        alarm.add_alarm_action(cw_actions.SnsAction(self.alert_topic))
        CfnOutput(self, "AlertTopicArn", value=self.alert_topic.topic_arn)

        # --- TriageElapsedMs: extracts `elapsed_ms` from `guardrail_triage` lines ---
        logs.MetricFilter(
            self, "TriageLatencyFilter",
            log_group=logs.LogGroup.from_log_group_name(self, "AgentLogsTriageLatency", agent_logs),
            filter_pattern=logs.FilterPattern.all(
                logs.FilterPattern.string_value("$.msg", "=", "guardrail_triage"),
            ),
            metric_namespace="VVA/Guardrail",
            metric_name="TriageElapsedMs",
            metric_value="$.elapsed_ms",
        )
        p90 = cw.Metric(
            namespace="VVA/Guardrail",
            metric_name="TriageElapsedMs",
            statistic="p90",
            period=Duration.hours(1),
        )
        latency_alarm = cw.Alarm(
            self, "TriageLatencyAlarm",
            alarm_name="vva-guardrail-triage-latency-p90",
            alarm_description=(
                f"vva-agent guardrail triage p90 above {TRIAGE_P90_MS} ms in 2 of the last 3 hours. "
                "DeepSeek endpoint or network latency has degraded; triage is on the critical path "
                "of every turn."
            ),
            metric=p90,
            threshold=TRIAGE_P90_MS,
            evaluation_periods=3,
            datapoints_to_alarm=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        latency_alarm.add_alarm_action(cw_actions.SnsAction(self.alert_topic))

        self.dashboard.add_widgets(
            cw.AlarmWidget(
                title=f"vva-agent: guardrail triage failures per hour (alarm at {TRIAGE_FAILURES_PER_HOUR})",
                alarm=alarm,
                width=8,
            ),
            cw.AlarmWidget(
                title=f"vva-agent: guardrail triage p90 latency (alarm at {TRIAGE_P90_MS} ms)",
                alarm=latency_alarm,
                width=8,
            ),
            cw.LogQueryWidget(
                title="vva-agent: guardrail triage calls and failures",
                log_group_names=[agent_logs],
                query_lines=[
                    'filter msg = "guardrail_triage" or msg = "guardrail_triage_failed"',
                    'stats count(*) as calls, sum(strcontains(msg, "failed")) as failed'
                    " by bin(1h)",
                ],
                view=cw.LogQueryVisualizationType.LINE,
                width=8,
            ),
        )

    def _memory_alarm(self) -> None:
        """Memory node latency → metric → alarm on the same topic, plus a dashboard row.

        The trigger for revisiting the no-Redis decision: two hours out of three
        with a p90 above MEMORY_P90_MS. Low traffic means few samples per hour,
        hence two of three rather than one.
        """
        agent_logs = "/aws/lambda/vva-agent"
        logs.MetricFilter(
            self, "MemoryLatencyFilter",
            log_group=logs.LogGroup.from_log_group_name(self, "AgentLogsMemory", agent_logs),
            filter_pattern=logs.FilterPattern.all(
                logs.FilterPattern.string_value("$.msg", "=", "node_complete"),
                logs.FilterPattern.string_value("$.node", "=", "memory"),
            ),
            metric_namespace="VVA/Memory",
            metric_name="NodeElapsedMs",
            metric_value="$.elapsed_ms",
        )
        p90 = cw.Metric(
            namespace="VVA/Memory",
            metric_name="NodeElapsedMs",
            statistic="p90",
            period=Duration.hours(1),
        )
        alarm = cw.Alarm(
            self, "MemorySlowAlarm",
            alarm_name=MEMORY_ALARM_NAME,
            alarm_description=(
                f"vva-agent memory node p90 above {MEMORY_P90_MS} ms in 2 of the last 3 hours. "
                "It reads recent messages from Neon with no cache (decision 17/10); "
                "see docs/plans/reupdate-plan.md D35 for what to do next."
            ),
            metric=p90,
            threshold=MEMORY_P90_MS,
            evaluation_periods=3,
            datapoints_to_alarm=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        alarm.add_alarm_action(cw_actions.SnsAction(self.alert_topic))
        self.dashboard.add_widgets(cw.AlarmWidget(
            title=f"vva-agent: memory node p90 (alarm at {MEMORY_P90_MS} ms)",
            alarm=alarm,
            width=8,
        ))
