# memory-benchmark-B3

Model: qwen/qwen3.8-27b via Groq. A và B cùng chạy trên model này nên so sánh hai thiết kế memory là công bằng, nhưng chất lượng, độ trễ và token KHÔNG đại diện cho prod (prod dùng DeepSeek trực tiếp).

- run 2026-10-16T11:06:18+07:00 phase=seed only=['latex_b'] key=llm=groq-qwen3.8-27b llm_calls=1 turns=0 probes=0
- run 2026-10-16T11:08:02+07:00 phase=seed only=['job_e'] key=llm=groq-qwen3.8-27b llm_calls=2 turns=0 probes=0
- run 2026-10-16T11:10:17+07:00 phase=seed only=['goal_f'] key=llm=groq-qwen3.8-27b llm_calls=1 turns=0 probes=0
- run 2026-10-16T11:12:13+07:00 phase=seed only=['instr_general_g'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=0 probes=1
- run 2026-10-16T11:14:47+07:00 phase=seed only=['instr_project_g'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=0 probes=2
- run 2026-10-16T11:17:20+07:00 phase=probes only=['latex_b'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=1 probes=3
- run 2026-10-16T11:19:10+07:00 phase=probes only=['job_e'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=2 probes=4
- run 2026-10-16T11:21:03+07:00 phase=probes only=['goal_f'] key=llm=groq-qwen3.8-27b llm_calls=1 turns=3 probes=5
- run 2026-10-16T11:22:55+07:00 phase=all only=['goal_f'] key=llm=groq-qwen3.8-27b llm_calls=1 turns=4 probes=5

3/4 probes passed (1 invalid, không chấm).

## Pass rate by group

| group | passed/total | invalid | rate |
|---|---|---|---|
| b | 1/1 | 0 | 1.0 |
| e | 1/1 | 0 | 1.0 |
| f | 0/1 | 1 | 0.0 |
| g | 1/2 | 0 | 0.5 |

## Memory node (ms)

- n=2, p50=6342, p95=6342

## Retrieval similarity (b/e vs f)

- b/e top: None values=[]
- f values=[]

## Background cost

- per turn: 0.0 LLM calls, 0.0 tokens
- avg turn total_tokens: 1576.5
- per 1,000 turns: {'background_tokens': 0, 'turn_tokens': 1576500}

## latex_b

### [b] PASS — Gợi ý vài bài tập với dây kháng lực cho tôi?

- where: new, session: 9d060286-aab7-5f77-bdd9-00ce45975023, attempt: 1, expect: ['latex'], expect_all: [], expect_tools: [], forbid: [], forbid_mode: None
- hit: ['latex'], all_hit: [], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: 86817f92-1ae2-4bc2-965a-614291230c5e
- mode: synthesize, tools: ['kb_search'], tools_missing: [], mode_hit: False, retrieval: memory_search=None, memory_ms=6249, to_done=16.83s, housekeep=0.0s, total_tokens=2508, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=None, skipped=None, embed_ms=42, user_facts=0, user_instructions=2
- answer: Dây kháng lực là lựa chọn tốt để tăng sức mạnh. Bạn dị ứng latex, nên khi chọn dây, hãy kiểm tra kỹ thành phần. **Tránh tuyệt đối các loại dây có chứa latex.**

Dưới đây là vài bài tập trong thư viện của ECA:

1. **Band push-up** (Ngực, cơ tay sau, vai)
   - Mức độ: Trung cấp
   - Nguồn: Thư viện bài tập ECA

2. **Single-arm band low row** (Cơ lưng rộng, cơ giữa lưng, lõi)
   - Mức độ: Trung cấp
   - Số lần: 8-15 lần mỗi set
   - Nguồn: Thư viện bài tập ECA

3. **Band lateral raise** (Vai)
   - Mức độ: Trung cấp
   - Số lần: 8-12 lần
   - Nguồn: Thư viện bài tập ECA

**Chống chỉ định:**
Nếu bạn đang gặp chấn thương cấp tính ở vùng cơ thể bài tập tác động (ví dụ: viêm khớp vai cấp tính khi tập Band lateral raise), hãy tạm dừng.

**Dấu hiệu cần dừng ngay:**
Đau tăng lên bất thường hoặc cảm giác khó chịu bất thường trong quá trình tập.

Bạn muốn bắt đầu với bài nào trước?

*Nguồn: thư viện bài tập của ECA — Band push-up, Single-arm band low row, Band lateral raise.*

*Đây là gợi ý, không thay thế khám lâm sàng.*

## job_e

### [e] PASS — Hiện giờ tôi làm nghề gì?

- where: last, session: 6c2d6346-1560-55f0-9f90-ca25ef0d7f60, attempt: 1, expect: ['giao vien'], expect_all: [], expect_tools: [], forbid: ['y ta'], forbid_mode: None
- hit: ['giao vien'], all_hit: [], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: 4e5725f7-53f9-4c02-823b-f5f15492d417
- mode: synthesize, tools: ['resume_last_session'], tools_missing: [], mode_hit: False, retrieval: memory_search=None, memory_ms=6342, to_done=20.11s, housekeep=0.0s, total_tokens=1281, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=None, skipped=None, embed_ms=29, user_facts=0, user_instructions=0
- answer: Bạn đang làm giáo viên.

## goal_f

### [f] INVALID — Thủ đô của Pháp là thành phố nào?

- where: new, session: bfbe22f8-5fab-5c2d-90b2-462b3526b143, attempt: 2, expect: [], expect_all: [], expect_tools: [], forbid: ['chay', '5km', 'xuan'], forbid_mode: None
- hit: [], all_hit: [], all_miss: [], leak: [], error: None, invalid: ['node_failed:planner'], answer_source: stream, request_id: 1192a63b-4c57-4b5c-a9e5-079689b981b7
- mode: None, tools: [], tools_missing: [], mode_hit: False, retrieval: tier2=0.7328960954752057, memory_ms=6201, to_done=13.51s, housekeep=0.0s, total_tokens=1260, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=0.7328960954752057, skipped=None, embed_ms=15, user_facts=0, user_instructions=0
- answer: Bạn hỏi gì đó? Mình là Anne ở đây, chuyên về vận động và thư giãn.

Mình không trả lời được câu hỏi địa lý đó. Nếu bạn cần tư vấn về bài tập thể dục, đau nhức hay muốn tìm một hoạt động phù hợp cho ngày hôm nay, hãy cho mình biết thêm chi tiết nhé.

## instr_general_g

### [g] PASS — general instruction seen in 3 conversations

- where: db, session: 67efadfa-74fc-5406-8ccf-0cf83e2b8e14, attempt: 1, expect: ['ngắn gọn'], expect_all: [], expect_tools: [], forbid: [], forbid_mode: None
- hit: [], all_hit: [], all_miss: [], leak: [], error: None, invalid: [], answer_source: db, request_id: None
- mode: None, tools: [], tools_missing: [], mode_hit: None, retrieval: db=None, memory_ms=None, to_done=Nones, housekeep=Nones, total_tokens=None, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=None, skipped=None, embed_ms=None, user_facts=None, user_instructions=None
- answer: Trả lời ngắn gọn [instruction/t1/active]

## instr_project_g

### [g] FAIL — project-A instruction seen in 3 conversations

- where: db, session: 3775aac9-40d0-55d0-a551-6899cd0ef29e, attempt: 1, expect: ['serverless'], expect_all: [], expect_tools: [], forbid: [], forbid_mode: None
- hit: [], all_hit: [], all_miss: [], leak: ["no row matching {'fact_contains': 'serverless', 'kind': 'instruction', 'tier': 2, 'status': 'active', 'project': 'A'}"], error: None, invalid: [], answer_source: db, request_id: None
- mode: None, tools: [], tools_missing: [], mode_hit: None, retrieval: db=None, memory_ms=None, to_done=Nones, housekeep=Nones, total_tokens=None, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=None, skipped=None, embed_ms=None, user_facts=None, user_instructions=None
- answer: Project A ưu tiên serverless [fact/t2/active/Project A]

