# memory-benchmark-N0

Model: qwen/qwen3.8-27b via Groq. A và B cùng chạy trên model này nên so sánh hai thiết kế memory là công bằng, nhưng chất lượng, độ trễ và token KHÔNG đại diện cho prod (prod dùng DeepSeek trực tiếp).

- run 2026-10-16T16:35:29+07:00 phase=seed only=['n_trust'] key=llm=deepseek llm_calls=1 turns=0 probes=0
- run 2026-10-16T16:37:55+07:00 phase=probes only=['n_trust'] key=llm=deepseek llm_calls=12 turns=4 probes=4

3/4 probes passed (0 invalid, không chấm).

## Pass rate by group

| group | passed/total | invalid | rate |
|---|---|---|---|
| n | 3/4 | 0 | 0.75 |

## Memory node (ms)

- n=4, p50=1633, p95=6185

## Retrieval similarity (b/e vs f)

- b/e top: None values=[]
- f values=[]

## Background cost

- per turn: 0.0 LLM calls, 0.0 tokens
- avg turn total_tokens: 1280.8
- per 1,000 turns: {'background_tokens': 0, 'turn_tokens': 1280750}

## n_trust

### [n] PASS — what am I preferred to be called?

- where: new, session: c317dbce-7f99-5942-9afb-42552f384b34, attempt: 1, expect: [], expect_all: [['mon']], expect_tools: [], forbid: ["can't confirm", 'cannot confirm', "can't verify", 'this device', 'someone else', 'might not be you', 'may not be you'], forbid_mode: None
- hit: [], all_hit: ['mon'], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: ce8bb44c-0cde-41a1-af06-f3d102ba617b
- mode: synthesize, tools: ['resume_last_session'], tools_missing: [], mode_hit: False, retrieval: tier2=0.8111227750778198, memory_ms=6185, to_done=18.89s, housekeep=0.0s, total_tokens=1231, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=0.8111227750778198, skipped=None, embed_ms=17, user_facts=0, user_instructions=0
- answer: You asked to be called Mon. I remember that from our earlier chat. How's your day going, Mon?

### [n] PASS — tôi thích được gọi là gì?

- where: new, session: 0764b2fd-1616-5394-9b8f-a32f7dd4255e, attempt: 1, expect: [], expect_all: [['mon']], expect_tools: [], forbid: ['không chắc', 'người khác', 'thiết bị này', 'không xác nhận'], forbid_mode: None
- hit: [], all_hit: ['mon'], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: d0baad5e-b5dc-485e-a786-3a8caca27e1f
- mode: synthesize, tools: ['resume_last_session'], tools_missing: [], mode_hit: False, retrieval: tier2=0.8448260426521301, memory_ms=1655, to_done=15.97s, housekeep=0.0s, total_tokens=1359, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=0.8448260426521301, skipped=None, embed_ms=17, user_facts=0, user_instructions=0
- answer: Bạn yêu cầu mình gọi bạn là Mon. Mình nhớ từ cuộc trò chuyện trước đó của chúng ta.

Ngày của bạn hôm nay thế nào, Mon?

### [n] PASS — who am I?

- where: new, session: ff1a6864-0424-5fd4-a0b0-c20f620932fc, attempt: 1, expect: [], expect_all: [['mon']], expect_tools: [], forbid: ["can't confirm", 'cannot confirm', "can't verify", 'this device', 'someone else', 'might not be you', 'may not be you'], forbid_mode: None
- hit: [], all_hit: ['mon'], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: 32d7a591-95b3-42ba-aaff-e8d6f353447b
- mode: synthesize, tools: ['resume_last_session'], tools_missing: [], mode_hit: False, retrieval: tier2=0.8391577005386353, memory_ms=1633, to_done=17.01s, housekeep=0.0s, total_tokens=1311, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=0.8391577005386353, skipped=None, embed_ms=16, user_facts=0, user_instructions=0
- answer: You are Mon. That is what you asked to be called in our last conversation. I remember it.

### [n] FAIL — do you know my name?

- where: new, session: 7535319e-9f76-565b-9a5b-761bf6c40b00, attempt: 1, expect: [], expect_all: [['mon']], expect_tools: [], forbid: ["can't confirm", 'cannot confirm', "can't verify", 'this device', 'someone else', 'might not be you', 'may not be you'], forbid_mode: None
- hit: [], all_hit: [], all_miss: [['mon']], leak: [], error: None, invalid: [], answer_source: stream, request_id: 0c9ac510-2fc9-461d-9ad2-b22a80d01cd6
- mode: chat, tools: ['recall_self'], tools_missing: [], mode_hit: False, retrieval: tier2=0.8291028636159234, memory_ms=1593, to_done=11.53s, housekeep=0.0s, total_tokens=1222, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=0.8291028636159234, skipped=None, embed_ms=14, user_facts=0, user_instructions=0
- answer: No. I can only see what you type here. I don't have access to your profile or any outside data. If you want me to know it, you have to tell me. Otherwise, I’m just talking to the person on the other side of the screen.

