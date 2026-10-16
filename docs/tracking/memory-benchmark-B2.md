# memory-benchmark-B2

Model: qwen/qwen3.8-27b via Groq. A và B cùng chạy trên model này nên so sánh hai thiết kế memory là công bằng, nhưng chất lượng, độ trễ và token KHÔNG đại diện cho prod (prod dùng DeepSeek trực tiếp).

- run 2026-10-16T09:45:18+07:00 phase=seed only=['back_a'] key=llm=groq-qwen3.8-27b llm_calls=1 turns=0 probes=0
- run 2026-10-16T09:47:02+07:00 phase=seed only=['latex_b'] key=llm=groq-qwen3.8-27b llm_calls=1 turns=0 probes=0
- run 2026-10-16T09:48:45+07:00 phase=seed only=['ice_c'] key=llm=groq-qwen3.8-27b llm_calls=1 turns=0 probes=0
- run 2026-10-16T09:50:26+07:00 phase=seed only=['knee_d'] key=llm=groq-qwen3.8-27b llm_calls=1 turns=0 probes=0
- run 2026-10-16T09:52:10+07:00 phase=seed only=['job_e'] key=llm=groq-qwen3.8-27b llm_calls=2 turns=0 probes=0
- run 2026-10-16T09:54:20+07:00 phase=seed only=['goal_f'] key=llm=groq-qwen3.8-27b llm_calls=1 turns=0 probes=0
- run 2026-10-16T09:56:44+07:00 phase=seed only=['long_k'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=0 probes=0
- run 2026-10-16T10:02:15+07:00 phase=seed only=['instr_general_g'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=0 probes=1
- run 2026-10-16T10:04:46+07:00 phase=seed only=['instr_project_g'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=0 probes=2
- run 2026-10-16T10:09:45+07:00 phase=seed only=['safe_once_h'] key=llm=groq-qwen3.8-27b llm_calls=1 turns=0 probes=3
- run 2026-10-16T10:11:45+07:00 phase=seed only=['name_churn_i'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=0 probes=4
- run 2026-10-16T10:14:21+07:00 phase=seed only=['forget_j'] key=llm=groq-qwen3.8-27b llm_calls=2 turns=0 probes=5
- run 2026-10-16T10:17:22+07:00 phase=seed only=['forget_all_j'] key=llm=groq-qwen3.8-27b llm_calls=2 turns=0 probes=6
- run 2026-10-16T10:19:37+07:00 phase=probes only=['back_a'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=1 probes=7
- run 2026-10-16T10:21:25+07:00 phase=probes only=['latex_b'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=2 probes=8
- run 2026-10-16T10:23:18+07:00 phase=probes only=['ice_c'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=3 probes=9
- run 2026-10-16T10:25:04+07:00 phase=probes only=['knee_d'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=4 probes=10
- run 2026-10-16T10:26:50+07:00 phase=probes only=['job_e'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=5 probes=11
- run 2026-10-16T10:28:40+07:00 phase=probes only=['goal_f'] key=llm=groq-qwen3.8-27b llm_calls=1 turns=6 probes=12
- run 2026-10-16T10:30:22+07:00 phase=all only=['goal_f'] key=llm=groq-qwen3.8-27b llm_calls=1 turns=7 probes=12

8/11 probes passed (1 invalid, không chấm).

## Pass rate by group

| group | passed/total | invalid | rate |
|---|---|---|---|
| a | 1/1 | 0 | 1.0 |
| b | 1/1 | 0 | 1.0 |
| c | 1/1 | 0 | 1.0 |
| d | 1/1 | 0 | 1.0 |
| e | 1/1 | 0 | 1.0 |
| f | 0/1 | 1 | 0.0 |
| g | 1/2 | 0 | 0.5 |
| h | 0/1 | 0 | 0.0 |
| i | 0/1 | 0 | 0.0 |
| j | 2/2 | 0 | 1.0 |

## Memory node (ms)

- n=5, p50=6902, p95=7007

## Retrieval similarity (b/e vs f)

- b/e top: None values=[]
- f values=[]

## Background cost

- per turn: 0.0 LLM calls, 0.0 tokens
- avg turn total_tokens: 1627.4
- per 1,000 turns: {'background_tokens': 0, 'turn_tokens': 1627429}

## back_a

### [a] PASS — Nhắc lại xem tôi bị đau ở đâu?

- where: new, session: b5f2ae54-8c6a-5efb-8569-9c66775a2793, attempt: 1, expect: ['lung'], expect_all: [], expect_tools: [], forbid: [], forbid_mode: None
- hit: ['lung'], all_hit: [], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: a71ff88f-1090-4d51-b20a-9cf8f15986c9
- mode: synthesize, tools: ['resume_last_session'], tools_missing: [], mode_hit: False, retrieval: memory_search=None, memory_ms=7007, to_done=19.14s, housekeep=0.0s, total_tokens=1322, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Bạn bị đau lưng dưới mỗi khi ngồi lâu.

## latex_b

### [b] PASS — Gợi ý vài bài tập với dây kháng lực cho tôi?

- where: new, session: 46f410b6-aa41-58b4-b78a-a81097db6795, attempt: 1, expect: ['latex'], expect_all: [], expect_tools: [], forbid: [], forbid_mode: None
- hit: ['latex'], all_hit: [], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: 46e7a5bb-3d52-4391-afe0-75f29b60962a
- mode: synthesize, tools: ['kb_search'], tools_missing: [], mode_hit: False, retrieval: memory_search=None, memory_ms=6902, to_done=17.71s, housekeep=0.0s, total_tokens=2619, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: **Lưu ý quan trọng: Bạn từng ghi nhận dị ứng latex. Hãy kiểm tra kỹ xuất xứ dây kháng lực. Chỉ chọn loại sợi tổng hợp (polymer) 100%, tuyệt đối không dùng dây làm từ latex tự nhiên.**

Thư viện ECA có sẵn 4 bài dùng dây kháng lực (tổng hợp, an toàn cho bạn), độ khó trung bình:

1. **Single-arm band low row** (Lưng giữa, Lats): Kéo dây cố định thấp, tập 8-15 reps mỗi set.
2. **Band lateral raise** (Vai): Nâng tay ngang, tập 8-12 reps.
3. **Band straight-arm pull-down** (Triceps, Lats): Kéo thẳng tay xuống, tập 8-15 reps hoặc hơn.
4. **Band push-up** (Ngực, Tam đầu): Đặt dây qua lưng, hai tay cầm đầu dây, tập theo nhịp.

**Chỉ định chống chỉ định (Contraindications):**
- **Tuyệt đối không tập nếu:** Dị ứng hoặc mẫn cảm với latex (như trường hợp của bạn nếu dùng dây latex).
- **Không tập nếu:** Đang có chấn thương cấp tính tại khớp vai, khuỷu tay, hoặc cổ tay.
- **Không tập nếu:** Đau ngực không rõ nguyên nhân, khó thở bất thường, hoặc tụt huyết áp khi vận động.

**Dấu hiệu phải DỪNG NGAY:**
- Đau nhói bất thường tại khớp hoặc cơ.
- Buồn nôn, chóng mặt, hoặc tim đập nhanh bất thường.
- **Nếu gặp bất kỳ dấu hiệu nào trên, buông dây, nghỉ ngơi và đi khám bác sĩ. Không tự ý tiếp tục tập.**

Bạn có muốn mình giải thích kỹ kỹ thuật bài nào không?

*Nguồn: thư viện bài tập của ECA — Band push-up, Single-arm band low row, Band straight-arm pull-down.*

*Đây là gợi ý, không thay thế khám lâm sàng.*

## ice_c

### [c] PASS — Hôm trước bạn dặn tôi gì về vụ chườm đá?

- where: new, session: 2f8588de-4b80-5bd5-9753-f4ff68743e8d, attempt: 1, expect: ['chuom', 'da'], expect_all: [], expect_tools: [], forbid: [], forbid_mode: None
- hit: ['chuom', 'da'], all_hit: [], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: e5b60e62-4884-4858-ad1a-bd00a2e2355e
- mode: synthesize, tools: ['resume_last_session', 'kb_search'], tools_missing: [], mode_hit: False, retrieval: memory_search=None, memory_ms=6877, to_done=20.21s, housekeep=0.0s, total_tokens=2355, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Mình dặn bạn chườm đá 15 phút mỗi tối và kê cao chân khi nghỉ.

Theo hướng dẫn của NHS, bạn nên dùng túi đá hoặc đậu hà lan đông gói trong khăn ấm áp lên đầu gối tối đa 20 phút, lặp lại mỗi 2 đến 3 giờ. Hãy để da tiếp xúc trực tiếp với bao đựng đá, không đặt đá trực tiếp lên da.

**Nếu đầu gối sưng nặng, biến dạng, không thể cử động hoặc chịu lực, hoặc bạn có sốt kèm đỏ nóng quanh khớp, hãy gọi ngay 111 hoặc đến trung tâm cấp cứu gần nhất để được khám.**

Bạn đang làm theo cách này chưa?

*Nguồn: hướng dẫn sức khỏe của NHS.*

*Đây là gợi ý, không thay thế khám lâm sàng.*

## knee_d

### [d] PASS — When did I have my left knee surgery?

- where: new, session: 2aad22dd-36c0-5290-9a35-e87621d9b09e, attempt: 1, expect: ['may', '2025'], expect_all: [], expect_tools: [], forbid: [], forbid_mode: None
- hit: ['may', '2025'], all_hit: [], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: da8794b3-118d-40eb-a5af-a93ff01e0bc5
- mode: synthesize, tools: ['resume_last_session'], tools_missing: [], mode_hit: False, retrieval: memory_search=None, memory_ms=6910, to_done=19.3s, housekeep=0.0s, total_tokens=1315, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: May 2025. That is the date I have on file for your left knee meniscus surgery.

## job_e

### [e] PASS — Hiện giờ tôi làm nghề gì?

- where: last, session: 9e0d7de8-9e2f-5586-8ffe-053cd15afbf2, attempt: 1, expect: ['giao vien'], expect_all: [], expect_tools: [], forbid: ['y ta'], forbid_mode: None
- hit: ['giao vien'], all_hit: [], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: ab534cc0-b989-4f10-8469-d01e19d3c5fa
- mode: synthesize, tools: ['resume_last_session'], tools_missing: [], mode_hit: False, retrieval: memory_search=None, memory_ms=6792, to_done=18.98s, housekeep=0.0s, total_tokens=1279, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Giáo viên.

## goal_f

### [f] INVALID — Thủ đô của Pháp là thành phố nào?

- where: new, session: 68a69ffb-cd8e-5f0c-bb07-c3c8c4e99738, attempt: 2, expect: [], expect_all: [], expect_tools: [], forbid: ['chay', '5km', 'xuan'], forbid_mode: None
- hit: [], all_hit: [], all_miss: [], leak: [], error: None, invalid: ['node_failed:planner'], answer_source: stream, request_id: be0552b7-e05d-4112-85c3-270213e3a405
- mode: None, tools: [], tools_missing: [], mode_hit: False, retrieval: memory_search=None, memory_ms=6913, to_done=14.05s, housekeep=0.0s, total_tokens=1250, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Câu này hơi lạc đề so với những gì mình làm ở đây. Mình là Anne, ở đây để nói chuyện về tập luyện và sức khỏe trong ứng dụng ECA.

Bạn có muốn hỏi về một bài tập cụ thể, hay đang gặp vấn đề nào về vận động không?

## long_k

- summary_check (stm): PASS, compactions=3, kept=['vai', 'ngan']/['vai', 'ngan'], chars=1432
- excerpt: Người dùng yêu cầu trả lời ngắn gọn. Người dùng bị đau vai phải khi giơ tay cao (tránh động tác quá đầu) và có tiền sử mổ gối. Các bài tập/lời khuyên đã cung cấp: Heel-slide, nâng chân thẳng, đi bộ, bong gân cổ chân, giãn cổ, dinh dưỡng/ngủ sau mổ gối, nâng bắp chân, đau gân kheo, tư thế ngồi, plank gối, khởi động đá bóng, uống nước mùa nóng, đạp xe tại chỗ, bơi lội, giãn đùi sau bằng khăn, tập tạ tay nhẹ, leo cầu thang. Mới đây, người dùng hỏi và được tư vấn về: đau mỏi vai gáy khi ngủ dậy (đổi gối, xoay cổ, chườm ấm, tránh bẻ cổ, thay nệm, hạn chế cúi điện thoại, bài rút cằm), squat nhẹ cho người đau gối (hạ 30 độ, vịn ghế, 8 lần x 2 đợt, thở đúng, tăng dần sau 3 tuần), khác biệt chườm nóng/lạnh (lạnh cho chấn thương mới/sưng, nóng cho cứng cơ mãn tính, thời gian 15-20 phút, lưu ý tiểu đ

## instr_general_g

### [g] PASS — general instruction seen in 3 conversations

- where: db, session: 7c9a8bf5-28d1-513e-9edb-7cba44187229, attempt: 1, expect: ['ngắn gọn'], expect_all: [], expect_tools: [], forbid: [], forbid_mode: None
- hit: [], all_hit: [], all_miss: [], leak: [], error: None, invalid: [], answer_source: db, request_id: None
- mode: None, tools: [], tools_missing: [], mode_hit: None, retrieval: db=None, memory_ms=None, to_done=Nones, housekeep=Nones, total_tokens=None, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Trả lời ngắn gọn [instruction/t1/active]

## instr_project_g

### [g] FAIL — project-A instruction seen in 3 conversations

- where: db, session: f20e6886-8a49-5d93-91b1-6482f7a74f8f, attempt: 1, expect: ['serverless'], expect_all: [], expect_tools: [], forbid: [], forbid_mode: None
- hit: [], all_hit: [], all_miss: [], leak: ["no row matching {'fact_contains': 'serverless', 'kind': 'instruction', 'tier': 2, 'status': 'active', 'project': 'A'}"], error: None, invalid: [], answer_source: db, request_id: None
- mode: None, tools: [], tools_missing: [], mode_hit: None, retrieval: db=None, memory_ms=None, to_done=Nones, housekeep=Nones, total_tokens=None, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Project A ưu tiên serverless [fact/t2/active/Project A]

## safe_once_h

### [h] FAIL — safety instruction said once

- where: db, session: a349c9c8-894e-59fe-b3f6-053fff5515d2, attempt: 1, expect: ['lưng'], expect_all: [], expect_tools: [], forbid: [], forbid_mode: None
- hit: [], all_hit: [], all_miss: [], leak: ["no row matching {'fact_contains': 'lưng', 'tier': 1, 'status': 'active'}"], error: None, invalid: [], answer_source: db, request_id: None
- mode: None, tools: [], tools_missing: [], mode_hit: None, retrieval: db=None, memory_ms=None, to_done=Nones, housekeep=Nones, total_tokens=None, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Do not suggest back exercises [instruction/t1/active]

## name_churn_i

### [i] FAIL — name changed across 3 conversations

- where: db, session: e57af6ff-e323-5100-b8a5-e843c66c5399, attempt: 1, expect: ['Long'], expect_all: [], expect_tools: [], forbid: [], forbid_mode: None
- hit: [], all_hit: [], all_miss: [], leak: ["no row matching {'fact_contains': 'Long', 'status': 'candidate', 'tier': 2}"], error: None, invalid: [], answer_source: db, request_id: None
- mode: None, tools: [], tools_missing: [], mode_hit: None, retrieval: db=None, memory_ms=None, to_done=Nones, housekeep=Nones, total_tokens=None, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Gọi tôi là Long [instruction/t2/active]

## forget_j

### [j] PASS — one item forgotten

- where: db, session: 9f441ef8-e7b7-531d-be4e-e0686d650d31, attempt: 1, expect: ['bơi lội'], expect_all: [], expect_tools: [], forbid: [], forbid_mode: None
- hit: [], all_hit: [], all_miss: [], leak: [], error: None, invalid: [], answer_source: db, request_id: None
- mode: None, tools: [], tools_missing: [], mode_hit: None, retrieval: db=None, memory_ms=None, to_done=Nones, housekeep=Nones, total_tokens=None, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: 

## forget_all_j

### [j] PASS — everything forgotten

- where: db, session: 2996515f-10ad-566c-b7db-34b5063c43db, attempt: 1, expect: ['y tá'], expect_all: [], expect_tools: [], forbid: [], forbid_mode: None
- hit: [], all_hit: [], all_miss: [], leak: [], error: None, invalid: [], answer_source: db, request_id: None
- mode: None, tools: [], tools_missing: [], mode_hit: None, retrieval: db=None, memory_ms=None, to_done=Nones, housekeep=Nones, total_tokens=None, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: 

