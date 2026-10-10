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

LLM judge alarm. When the grader's LLM judge fails (nodes/grader.py::_judge),
the turn passes unchecked by design, so a judge that is down for everyone looks
the same as a healthy one from the outside. Each failure logs
`grader_judge_failed`; a metric filter on the vva-agent log group counts them,
and 3 in one hour sets off `vva-grader-judge-failed`, which publishes to the
`vva-alerts` topic. One or two timeouts while DeepSeek is slow are expected and
cost nothing but the check; three in an hour means the judge is down for
whoever is using the app.

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
JUDGE_ALARM_NAME = "vva-grader-judge-failed"
JUDGE_FAILURES_PER_HOUR = 3

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

        self._judge_alarm()

    def _judge_alarm(self) -> None:
        """grader_judge_failed → metric → alarm → topic, plus one dashboard row."""
        agent_logs = "/aws/lambda/vva-agent"
        logs.MetricFilter(
            self, "JudgeFailedFilter",
            log_group=logs.LogGroup.from_log_group_name(self, "AgentLogs", agent_logs),
            filter_pattern=logs.FilterPattern.all_terms("grader_judge_failed"),
            metric_namespace="VVA/Grader",
            metric_name="JudgeFailed",
            metric_value="1",
        )
        failures = cw.Metric(
            namespace="VVA/Grader",
            metric_name="JudgeFailed",
            statistic="Sum",
            period=Duration.hours(1),
        )
        self.alert_topic = sns.Topic(self, "AlertTopic", topic_name=ALERT_TOPIC_NAME)
        alarm = cw.Alarm(
            self, "JudgeFailedAlarm",
            alarm_name=JUDGE_ALARM_NAME,
            alarm_description=(
                f"{JUDGE_FAILURES_PER_HOUR}+ grader_judge_failed in one hour on vva-agent: "
                "turns are passing without the LLM judge. Logs: /aws/lambda/vva-agent, "
                "field `error`."
            ),
            metric=failures,
            threshold=JUDGE_FAILURES_PER_HOUR,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        alarm.add_alarm_action(cw_actions.SnsAction(self.alert_topic))
        CfnOutput(self, "AlertTopicArn", value=self.alert_topic.topic_arn)

        self.dashboard.add_widgets(
            cw.AlarmWidget(
                title=f"vva-agent: LLM judge failures per hour (alarm at {JUDGE_FAILURES_PER_HOUR})",
                alarm=alarm,
                width=8,
            ),
            cw.LogQueryWidget(
                title="vva-agent: LLM judge calls and failures",
                log_group_names=[agent_logs],
                query_lines=[
                    'filter msg = "grader_judge" or msg = "grader_judge_failed"',
                    'stats count(*) as calls, sum(strcontains(msg, "failed")) as failed'
                    " by bin(1h)",
                ],
                view=cw.LogQueryVisualizationType.LINE,
                width=8,
            ),
        )
