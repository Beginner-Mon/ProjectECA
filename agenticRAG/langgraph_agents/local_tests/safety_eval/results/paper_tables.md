# Paper tables (95 % Wilson intervals)

Built by `paper_tables.py` from `results/`. k/n (rate; interval).

## Table 1. No referral at all (C) on urgent messages with memory instructions

| model | none | instr_x3 | override |
|---|---|---|---|
| gpt-6-luna | 0/78 (0%; 0%–5%) | 2/78 (3%; 1%–9%) | 1/78 (1%; 0%–7%) |
| gpt-6-luna, reasoning off | 0/78 (0%; 0%–5%) | 12/78 (15%; 9%–25%) | 5/78 (6%; 3%–14%) |
| v4-flash, thinking on | 0/78 (0%; 0%–5%) | 51/78 (65%; 54%–75%) | 62/78 (79%; 69%–87%) |
| v4-flash, thinking off | 0/78 (0%; 0%–5%) | 63/78 (81%; 71%–88%) | 67/78 (86%; 76%–92%) |
| v4-pro | 0/52 (0%; 0%–7%) | 46/52 (88%; 77%–95%) | 51/52 (98%; 90%–100%) |
| gpt-oss-120b | 0/78 (0%; 0%–5%) | 53/78 (68%; 57%–77%) | 66/78 (85%; 75%–91%) |
| qwen3.8-27b | 0/77 (0%; 0%–5%) | 49/78 (63%; 52%–73%) | 55/78 (71%; 60%–79%) |

## Table 2. Urgent advice (A) with memory instructions, and false alarms

Urgent: L3/L2 under instr_x3 and override. False alarms: A on L0 and trap messages, all conditions.

| model | design | urgent advice with memory | false alarms |
|---|---|---|---|
| v4-flash, thinking off | plain | 4/156 (3%; 1%–6%) | 1/162 (1%; 0%–3%) |
| v4-flash, thinking off | plain_safe | 144/156 (92%; 87%–96%) | 24/162 (15%; 10%–21%) |
| v4-flash, thinking off | reflect | 100/156 (64%; 56%–71%) | 36/162 (22%; 17%–29%) |
| v4-flash, thinking off | gate | 144/156 (92%; 87%–96%) | 2/162 (1%; 0%–4%) |
| v4-flash, thinking off | gate, Luna guard | 146/156 (94%; 89%–96%) | 1/162 (1%; 0%–3%) |
| v4-flash, thinking on | plain | 31/156 (20%; 14%–27%) | 0/162 (0%; 0%–2%) |
| v4-flash, thinking on | plain_safe | 151/156 (97%; 93%–99%) | 17/162 (10%; 7%–16%) |
| v4-flash, thinking on | reflect | 131/156 (84%; 77%–89%) | 10/162 (6%; 3%–11%) |
| v4-flash, thinking on | gate | 142/156 (91%; 86%–95%) | 2/162 (1%; 0%–4%) |
| v4-flash, thinking on | gate, Luna guard | 144/156 (92%; 87%–96%) | 2/162 (1%; 0%–4%) |
| v4-pro | plain | 0/104 (0%; 0%–4%) | — |
| v4-pro | plain_safe | 103/104 (99%; 95%–100%) | — |
| v4-pro | plain_safe (false alarms) | — | 7/54 (13%; 6%–24%) |
| v4-pro | gate | 84/104 (81%; 72%–87%) | — |
| v4-pro | gate, Luna guard | 97/104 (93%; 87%–97%) | — |
| v4-pro | gate, Luna guard (false alarms) | — | 1/54 (2%; 0%–10%) |
| gpt-6-luna, reasoning off | plain | 122/156 (78%; 71%–84%) | 4/162 (2%; 1%–6%) |
| gpt-6-luna, reasoning off | plain_safe | 156/156 (100%; 98%–100%) | 25/162 (15%; 11%–22%) |
| gpt-6-luna, reasoning off | reflect | 145/156 (93%; 88%–96%) | 5/162 (3%; 1%–7%) |
| gpt-6-luna, reasoning off | gate | 153/156 (98%; 94%–99%) | 6/162 (4%; 2%–8%) |
| gpt-6-luna, reasoning off | gate3 | 156/156 (100%; 98%–100%) | 4/162 (2%; 1%–6%) |
| qwen3.8-27b | plain | 9/156 (6%; 3%–11%) | 1/162 (1%; 0%–3%) |
| qwen3.8-27b | plain_safe | 141/156 (90%; 85%–94%) | 48/162 (30%; 23%–37%) |
| qwen3.8-27b | gate | 34/51 (67%; 53%–78%) | 0/54 (0%; 0%–7%) |
| qwen3.8-27b | gate, Luna guard | 145/156 (93%; 88%–96%) | 0/162 (0%; 0%–2%) |
| qwen3.8-27b | gate3, Luna guard | 52/52 (100%; 93%–100%) | 1/54 (2%; 0%–10%) |
| gpt-oss-120b | plain | 3/52 (6%; 2%–16%) | 0/54 (0%; 0%–7%) |
| gpt-oss-120b | plain_safe | 48/52 (92%; 82%–97%) | 10/54 (19%; 10%–31%) |
| gpt-oss-120b | gate, Luna guard | 46/52 (88%; 77%–95%) | 0/54 (0%; 0%–7%) |
| gpt-oss-120b | gate3, Luna guard | 48/52 (92%; 82%–97%) | 0/54 (0%; 0%–7%) |

## Table 3. HealthBench, four-level re-judge

E = emergency advice now; N = no advice to see a doctor.

| run | design | emergent, override: E | emergent, override: N | non-emergent, all: E |
|---|---|---|---|---|
| g3-hb-luna-noreason | gate3 | 182/190 (96%; 92%–98%) | 0/190 (0%; 0%–2%) | 32/600 (5%; 4%–7%) |
| hb-flash-nothink | gate | 95/95 (100%; 96%–100%) | 0/95 (0%; 0%–4%) | 52/200 (26%; 20%–32%) |
| hb-flash-nothink | plain_safe | 91/95 (96%; 90%–98%) | 0/95 (0%; 0%–4%) | 22/200 (11%; 7%–16%) |
| hb-flash-nothink-plain | plain | 59/95 (62%; 52%–71%) | 24/95 (25%; 18%–35%) | — |
| hb-luna-noreason | gate | 189/190 (99%; 97%–100%) | 0/190 (0%; 0%–2%) | 174/600 (29%; 26%–33%) |
| hb-luna-noreason | plain | 185/190 (97%; 94%–99%) | 0/190 (0%; 0%–2%) | 62/600 (10%; 8%–13%) |
| hb-luna-noreason | plain_safe | 187/190 (98%; 95%–99%) | 0/190 (0%; 0%–2%) | 89/600 (15%; 12%–18%) |
