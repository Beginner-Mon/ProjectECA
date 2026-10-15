# memory-benchmark-B

Model: qwen/qwen3.8-27b via Groq. A và B cùng chạy trên model này nên so sánh hai thiết kế memory là công bằng, nhưng chất lượng, độ trễ và token KHÔNG đại diện cho prod (prod dùng DeepSeek trực tiếp).

- run 2026-10-15T13:35:27+07:00 phase=seed only=['back_a'] key=llm=deepseek llm_calls=1 turns=0 probes=0
- run 2026-10-15T13:37:17+07:00 phase=seed only=['latex_b'] key=llm=deepseek llm_calls=1 turns=0 probes=0
- run 2026-10-15T13:39:09+07:00 phase=seed only=['ice_c'] key=llm=deepseek llm_calls=1 turns=0 probes=0
- run 2026-10-15T13:41:04+07:00 phase=seed only=['knee_d'] key=llm=deepseek llm_calls=1 turns=0 probes=0
- run 2026-10-15T13:42:58+07:00 phase=seed only=['job_e'] key=llm=deepseek llm_calls=2 turns=0 probes=0
- run 2026-10-15T13:46:41+07:00 phase=seed only=['goal_f'] key=llm=deepseek llm_calls=1 turns=0 probes=0
- run 2026-10-15T13:48:36+07:00 phase=seed only=['long_k'] key=llm=deepseek llm_calls=3 turns=0 probes=0
- run 2026-10-15T13:54:15+07:00 phase=seed only=['instr_general_g'] key=llm=deepseek llm_calls=3 turns=0 probes=1
- run 2026-10-15T13:56:55+07:00 phase=seed only=['instr_project_g'] key=llm=deepseek llm_calls=3 turns=0 probes=2
- run 2026-10-15T14:00:27+07:00 phase=seed only=['safe_once_h'] key=llm=deepseek llm_calls=1 turns=0 probes=3
- run 2026-10-15T14:03:40+07:00 phase=seed only=['name_churn_i'] key=llm=deepseek llm_calls=3 turns=0 probes=4
- run 2026-10-15T14:06:47+07:00 phase=seed only=['forget_j'] key=llm=deepseek llm_calls=2 turns=0 probes=5
- run 2026-10-15T14:09:06+07:00 phase=seed only=['forget_all_j'] key=llm=deepseek llm_calls=2 turns=0 probes=6
- run 2026-10-15T14:11:27+07:00 phase=probes only=['back_a'] key=llm=deepseek llm_calls=3 turns=1 probes=7
- run 2026-10-15T14:13:18+07:00 phase=probes only=['latex_b'] key=llm=deepseek llm_calls=3 turns=2 probes=8
- run 2026-10-15T14:16:15+07:00 phase=probes only=['ice_c'] key=llm=deepseek llm_calls=3 turns=3 probes=9
- run 2026-10-15T14:18:08+07:00 phase=probes only=['knee_d'] key=llm=deepseek llm_calls=3 turns=4 probes=10
- run 2026-10-15T14:20:00+07:00 phase=probes only=['job_e'] key=llm=deepseek llm_calls=3 turns=5 probes=11
- run 2026-10-15T14:22:07+07:00 phase=probes only=['goal_f'] key=llm=deepseek llm_calls=1 turns=6 probes=12
- run 2026-10-15T14:24:16+07:00 phase=all only=all key=llm=deepseek llm_calls=1 turns=7 probes=12
- run 2026-10-15T15:01:14+07:00 phase=seed only=['instr_general_g'] key=llm=deepseek llm_calls=3 turns=7 probes=12
- run 2026-10-15T15:03:58+07:00 phase=seed only=['instr_project_g'] key=llm=deepseek llm_calls=3 turns=7 probes=12
- run 2026-10-15T15:06:34+07:00 phase=seed only=['safe_once_h'] key=llm=deepseek llm_calls=1 turns=7 probes=12
- run 2026-10-15T15:09:03+07:00 phase=seed only=['name_churn_i'] key=llm=deepseek llm_calls=3 turns=7 probes=12
- run 2026-10-15T15:11:53+07:00 phase=seed only=['forget_j'] key=llm=deepseek llm_calls=2 turns=7 probes=12
- run 2026-10-15T15:14:10+07:00 phase=seed only=['forget_all_j'] key=llm=deepseek llm_calls=2 turns=7 probes=12

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

- n=5, p50=6911, p95=7171

## Retrieval similarity (b/e vs f)

- b/e top: None values=[]
- f values=[]

## Background cost

- per turn: 0.0 LLM calls, 0.0 tokens
- avg turn total_tokens: 1602.0
- per 1,000 turns: {'background_tokens': 0, 'turn_tokens': 1602000}

## back_a

### [a] PASS — Nhắc lại xem tôi bị đau ở đâu?

- where: new, session: 43e418cb-01d6-5662-b570-b7612909ef47, attempt: 1, expect: ['lung'], forbid: []
- hit: ['lung'], leak: [], error: None, invalid: [], answer_source: stream, request_id: b899c5e0-9452-4d36-bfba-1c6749f98e00
- retrieval: memory_search=None, memory_ms=7171, to_done=20.66s, housekeep=0.0s, total_tokens=1335, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Bạn đau ở vùng lưng dưới, thường xuất hiện mỗi khi ngồi lâu. Mình đã ghi nhớ thông tin này để tránh gợi ý các động tác gây áp lực lên khu vực đó.

## latex_b

### [b] PASS — Gợi ý vài bài tập với dây kháng lực cho tôi?

- where: new, session: 25f3bc80-e953-571c-961f-4acc17ca8cda, attempt: 1, expect: ['latex'], forbid: []
- hit: ['latex'], leak: [], error: None, invalid: [], answer_source: stream, request_id: 00abcaf1-07a2-4eaa-bda8-30068ecca095
- retrieval: memory_search=None, memory_ms=6911, to_done=19.03s, housekeep=0.0s, total_tokens=2457, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Chào bạn. Trước khi bắt đầu, mình phải nói thẳng: bạn dị ứng latex. **Dây kháng lực làm từ latex là chống chỉ định tuyệt đối với bạn.** Nếu dây của bạn có mùi cao su đặc trưng hoặc nguồn gốc không rõ, hãy dừng lại ngay và mang đi kiểm tra hoặc thay bằng loại nylon, TPE.

Thư viện ECA có một số bài dùng dây (bands), nhưng bạn cần chắc chắn chất liệu không phải latex. Dưới đây là vài gợi ý từ thư viện:

1. **Band push-up (Ngực):** Bài nâng cấp của hít đất. Dây tạo lực động khớp với đường sức, tập trung vào ngực, tam đầu và vai.
2. **Single-arm band low row (Lưng giữa):** Kéo dây neo thấp, tập tay một bên. Tác động lên cơ lưng rộng và cơ thang. Thường làm với nhịp 8-15 lần mỗi hiệp, đặt ở giai đoạn sau của buổi tập lưng.
3. **Band overhead triceps extension (Tam đầu duỗi tay trên đầu):** Bài đơn khớp, nhắm vào phần đầu dài của cơ tam đầu. Thường làm cuối buổi tập tam đầu, nhịp 8-12 lần trở lên mỗi hiệp.
4. **Lateral Raise - With Bands (Vai):** Nâng tay sang hai bên, mức độ khó dành cho người mới bắt đầu.

**Lưu ý an toàn:**
**Dừng tập ngay nếu thấy ngứa, đỏ da, khó thở hoặc bất kỳ phản ứng dị ứng nào.**
Nếu chưa rõ loại dây mình có đang dùng có chứa latex không, hãy kiểm tra kỹ nhãn hoặc hỏi nhà cung cấp trước khi tiếp xúc da.

Bạn có đang sở hữu loại dây nào cụ thể không?

*Nguồn: thư viện bài tập của ECA — Band overhead triceps extension, Band push-up, Lateral Raise - With Bands.*

*Đây là gợi ý, không thay thế khám lâm sàng.*

## ice_c

### [c] PASS — Hôm trước bạn dặn tôi gì về vụ chườm đá?

- where: new, session: 5e7205d1-99e5-5ac6-8728-8954055c6cba, attempt: 1, expect: ['chuom', 'da'], forbid: []
- hit: ['chuom', 'da'], leak: [], error: None, invalid: [], answer_source: stream, request_id: 780dc448-8205-477f-ae43-7632a2034745
- retrieval: memory_search=None, memory_ms=7026, to_done=21.69s, housekeep=0.0s, total_tokens=2397, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Lúc trước mình dặn bạn chườm đá 15 phút mỗi tối và kê cao chân khi nghỉ.

Theo hướng dẫn của NHS, quy cách chườm đá cụ thể là dùng túi đá hoặc đậu Hà Lan đông lạnh bọc trong khăn, áp lên đầu gối tối đa 20 phút, thực hiện mỗi 2 đến 3 giờ một lần. Bạn cũng có thể dùng thuốc giảm đau như paracetamol hoặc gel ibuprofen để hỗ trợ.

**Nếu đầu gối sưng nặng hơn, thay đổi hình dạng, không thể di chuyển, đau dữ dội, hoặc bạn có sốt kèm theo vùng da quanh đầu gối đỏ và nóng, hãy liên hệ ngay với dịch vụ y tế khẩn cấp (tại UK là 111) hoặc đi khám ngay. Đây có thể là dấu hiệu nhiễm trùng hoặc chấn thương nghiêm trọng.**

Bạn đang thấy các triệu chứng này không?

*Nguồn: hướng dẫn sức khỏe của NHS.*

*Đây là gợi ý, không thay thế khám lâm sàng.*

## knee_d

### [d] PASS — When did I have my left knee surgery?

- where: new, session: 6f12df4a-965c-5fd2-8030-40875f64a2aa, attempt: 1, expect: ['may', '2025'], forbid: []
- hit: ['may', '2025'], leak: [], error: None, invalid: [], answer_source: stream, request_id: 4cc96cc2-97f6-4fa2-81c3-6bec7b33baee
- retrieval: memory_search=None, memory_ms=6759, to_done=20.44s, housekeep=0.0s, total_tokens=1304, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: You had meniscus surgery on your left knee in May 2025. I have that noted in your health history.

## job_e

### [e] PASS — Hiện giờ tôi làm nghề gì?

- where: last, session: 23cee912-71d9-57b0-8c16-85a0fa5c50af, attempt: 1, expect: ['giao vien'], forbid: ['y ta']
- hit: ['giao vien'], leak: [], error: None, invalid: [], answer_source: stream, request_id: 50468632-93b5-4877-ba1c-b500b48c48a9
- retrieval: memory_search=None, memory_ms=6882, to_done=20.32s, housekeep=0.0s, total_tokens=1278, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Giáo viên.

## goal_f

### [f] INVALID — Thủ đô của Pháp là thành phố nào?

- where: new, session: 68ba103f-2bab-5892-93e5-6428e07a8656, attempt: 2, expect: [], forbid: ['chay', '5km', 'xuan']
- hit: [], leak: [], error: None, invalid: ['node_failed:planner'], answer_source: stream, request_id: 88425163-5d17-499c-8b79-5b10b76468bb
- retrieval: memory_search=None, memory_ms=6943, to_done=14.62s, housekeep=0.0s, total_tokens=1207, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Bạn hỏi地理, mình không rành mảng này. Bạn đang muốn tìm lịch sử hay địa lý không? Mình chỉ hỗ trợ về luyện tập và sức khỏe thôi.

## long_k

- summary_check (stm): PASS, compactions=3, kept=['vai', 'ngan']/['vai', 'ngan'], chars=1432
- excerpt: Người dùng yêu cầu trả lời ngắn gọn. Người dùng bị đau vai phải khi giơ tay cao (tránh động tác quá đầu) và có tiền sử mổ gối. Các bài tập/lời khuyên đã cung cấp: Heel-slide, nâng chân thẳng, đi bộ, bong gân cổ chân, giãn cổ, dinh dưỡng/ngủ sau mổ gối, nâng bắp chân, đau gân kheo, tư thế ngồi, plank gối, khởi động đá bóng, uống nước mùa nóng, đạp xe tại chỗ, bơi lội, giãn đùi sau bằng khăn, tập tạ tay nhẹ, leo cầu thang. Mới đây, người dùng hỏi và được tư vấn về: đau mỏi vai gáy khi ngủ dậy (đổi gối, xoay cổ, chườm ấm, tránh bẻ cổ, thay nệm, hạn chế cúi điện thoại, bài rút cằm), squat nhẹ cho người đau gối (hạ 30 độ, vịn ghế, 8 lần x 2 đợt, thở đúng, tăng dần sau 3 tuần), khác biệt chườm nóng/lạnh (lạnh cho chấn thương mới/sưng, nóng cho cứng cơ mãn tính, thời gian 15-20 phút, lưu ý tiểu đ

## instr_general_g

### [g] PASS — general instruction seen in 3 conversations

- where: db, session: 7853c9b5-f8af-58b4-b9dd-84ab050663dd, attempt: 1, expect: ['ngắn gọn'], forbid: []
- hit: [], leak: [], error: None, invalid: [], answer_source: db, request_id: None
- retrieval: db=None, memory_ms=None, to_done=Nones, housekeep=Nones, total_tokens=None, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Trả lời ngắn gọn [instruction/t1/active]

## instr_project_g

### [g] FAIL — project-A instruction seen in 3 conversations

- where: db, session: f9f668a3-1d6e-5804-a449-04195518897e, attempt: 1, expect: ['serverless'], forbid: []
- hit: [], leak: ["no row matching {'fact_contains': 'serverless', 'kind': 'instruction', 'tier': 2, 'status': 'active', 'project': 'A'}"], error: None, invalid: [], answer_source: db, request_id: None
- retrieval: db=None, memory_ms=None, to_done=Nones, housekeep=Nones, total_tokens=None, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Project A ưu tiên kiến trúc serverless [fact/t2/active/Project A]

## safe_once_h

### [h] FAIL — safety instruction said once

- where: db, session: 73a784eb-9994-5376-80c9-fe4037e338c2, attempt: 1, expect: ['lưng'], forbid: []
- hit: [], leak: ["no row matching {'fact_contains': 'lưng', 'tier': 1, 'status': 'active'}"], error: None, invalid: [], answer_source: db, request_id: None
- retrieval: db=None, memory_ms=None, to_done=Nones, housekeep=Nones, total_tokens=None, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Do not suggest back exercises [instruction/t1/active]

## name_churn_i

### [i] FAIL — name changed across 3 conversations

- where: db, session: 4ea7f68d-62f7-5d73-b5e5-352937c9ddb2, attempt: 1, expect: ['Long'], forbid: []
- hit: [], leak: ["no row matching {'fact_contains': 'Long', 'status': 'candidate', 'tier': 2}"], error: None, invalid: [], answer_source: db, request_id: None
- retrieval: db=None, memory_ms=None, to_done=Nones, housekeep=Nones, total_tokens=None, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Gọi tôi là Long [instruction/t2/active]

## forget_j

### [j] PASS — one item forgotten

- where: db, session: 9a687aac-4fda-5899-a551-433c18d1a068, attempt: 1, expect: ['bơi lội'], forbid: []
- hit: [], leak: [], error: None, invalid: [], answer_source: db, request_id: None
- retrieval: db=None, memory_ms=None, to_done=Nones, housekeep=Nones, total_tokens=None, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: 

## forget_all_j

### [j] PASS — everything forgotten

- where: db, session: 086ea21f-e40a-57e1-84ef-2508eaa5b1b0, attempt: 1, expect: ['y tá'], forbid: []
- hit: [], leak: [], error: None, invalid: [], answer_source: db, request_id: None
- retrieval: db=None, memory_ms=None, to_done=Nones, housekeep=Nones, total_tokens=None, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: 

