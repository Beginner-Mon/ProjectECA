# memory-benchmark-N1

Model: qwen/qwen3.8-27b via Groq. A và B cùng chạy trên model này nên so sánh hai thiết kế memory là công bằng, nhưng chất lượng, độ trễ và token KHÔNG đại diện cho prod (prod dùng DeepSeek trực tiếp).

- run 2026-10-16T16:42:46+07:00 phase=seed only=['n_trust'] key=llm=deepseek llm_calls=1 turns=0 probes=0
- run 2026-10-16T16:44:50+07:00 phase=probes only=['n_trust'] key=llm=deepseek llm_calls=10 turns=4 probes=4

4/4 probes passed (0 invalid, không chấm).

## Pass rate by group

| group | passed/total | invalid | rate |
|---|---|---|---|
| n | 4/4 | 0 | 1.0 |

## Memory node (ms)

- n=4, p50=1565, p95=6333

## Retrieval similarity (b/e vs f)

- b/e top: None values=[]
- f values=[]

## Background cost

- per turn: 0.0 LLM calls, 0.0 tokens
- avg turn total_tokens: 1185.0
- per 1,000 turns: {'background_tokens': 0, 'turn_tokens': 1185000}

## n_trust

### [n] PASS — what am I preferred to be called?

- where: new, session: 5a9d0d43-3e14-5496-a14d-54698d1e5fa7, attempt: 1, expect: [], expect_all: [['mon']], expect_tools: [], forbid: ["can't confirm", 'cannot confirm', "can't verify", 'this device', 'someone else', 'might not be you', 'may not be you'], forbid_mode: None
- hit: [], all_hit: ['mon'], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: 9243aadc-9567-4e21-a676-99b177e19842
- mode: chat, tools: [], tools_missing: [], mode_hit: False, retrieval: tier2=0.8111227750778198, memory_ms=6333, to_done=14.18s, housekeep=0.0s, total_tokens=1103, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=0.8111227750778198, skipped=None, embed_ms=18, user_facts=1, user_instructions=0
- answer: Mon. You told me that just this morning. I remember.

### [n] PASS — tôi thích được gọi là gì?

- where: new, session: 676bb7b5-8217-58d1-8eab-c128444d67a8, attempt: 1, expect: [], expect_all: [['mon']], expect_tools: [], forbid: ['không chắc', 'người khác', 'thiết bị này', 'không xác nhận'], forbid_mode: None
- hit: [], all_hit: ['mon'], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: 3d3e43cb-c5fd-4aea-843a-ebaea669eeca
- mode: chat, tools: [], tools_missing: [], mode_hit: False, retrieval: tier2=0.8448260426521301, memory_ms=1565, to_done=8.72s, housekeep=0.0s, total_tokens=1137, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=0.8448260426521301, skipped=None, embed_ms=16, user_facts=1, user_instructions=0
- answer: Bạn muốn mình gọi bạn là Mon, đúng không?

### [n] PASS — who am I?

- where: new, session: 1302ace9-8327-5db0-8106-d3406eeaad6b, attempt: 1, expect: [], expect_all: [['mon']], expect_tools: [], forbid: ["can't confirm", 'cannot confirm', "can't verify", 'this device', 'someone else', 'might not be you', 'may not be you'], forbid_mode: None
- hit: [], all_hit: ['mon'], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: caa6d492-3ab7-41f6-94ab-0857dfa93088
- mode: chat, tools: ['recall_self'], tools_missing: [], mode_hit: False, retrieval: tier2=0.8391577005386353, memory_ms=1574, to_done=10.34s, housekeep=0.0s, total_tokens=1241, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=0.8391577005386353, skipped=None, embed_ms=14, user_facts=1, user_instructions=0
- answer: You are Mon. That is the name you told me today.

### [n] PASS — do you know my name?

- where: new, session: bed12825-f0b4-5636-b0f9-d8e6ab8c782d, attempt: 1, expect: [], expect_all: [['mon']], expect_tools: [], forbid: ["can't confirm", 'cannot confirm', "can't verify", 'this device', 'someone else', 'might not be you', 'may not be you'], forbid_mode: None
- hit: [], all_hit: ['mon'], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: 9a908ea3-e6df-466c-b85b-b8d35187acb1
- mode: chat, tools: ['recall_self'], tools_missing: [], mode_hit: False, retrieval: tier2=0.8291028636159234, memory_ms=1314, to_done=9.63s, housekeep=0.0s, total_tokens=1259, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=0.8291028636159234, skipped=None, embed_ms=14, user_facts=1, user_instructions=0
- answer: Yes, you are Mon.

