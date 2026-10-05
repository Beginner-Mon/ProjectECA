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
"""

from aws_cdk import Duration, Stack
from aws_cdk import aws_cloudwatch as cw
from constructs import Construct

DASHBOARD_NAME = "vva-lambdas"

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
