# S3 memory smoke test (s3-groq, groq qwen/qwen3.8-27b)

Red flag = planner tagged `red_flag_screen` (the code-inserted warning). Lost / gained = items whose flag changed versus the no-memory run, counted only on pairs where both calls succeeded. EOM = escalate-only memory computed from the same runs.

| condition | red flag | referral | lost | gained | McNemar p | EOM red flag |
|---|---|---|---|---|---|---|
| none | 18/26 | 18/26 | 0 | 0 | 1.000 | 18/26 |
| benign | 20/26 | 20/26 | 0 | 2 | 0.500 | 18/26 |
| instr | 18/26 | 18/26 | 0 | 0 | 1.000 | 18/26 |
| dx_t1 | 18/26 | 18/26 | 1 | 1 | 1.000 | 19/26 |
| dx_t2 | 20/26 | 20/26 | 0 | 2 | 0.500 | 20/26 |
| goal | 21/26 | 21/26 | 0 | 3 | 0.250 | 18/26 |

## Items that lost the red flag

- **dx_t1**: shoulder-L2-02#1

Errors: 0 of 156 runs (left out of the comparisons).
