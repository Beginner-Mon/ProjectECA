# memory-benchmark-A

Model: **qwen/qwen3.8-27b qua Groq, cho mọi role** (DEEPSEEK_BASE_URL trỏ sang Groq bằng biến môi trường; dòng `key=llm=deepseek` bên dưới là nhãn của helper, không phải model thật). Chạy từ worktree cố định ở 15fcb77b (+ bản sửa llm.py của d0f826bb), ngày 15/10/2026, vì API của DeepSeek sập và Gemini hết hạn mức. B phải chạy cùng model này. Chất lượng, độ trễ và token không đại diện cho prod (prod dùng DeepSeek).

- run 2026-10-15T12:02:07+07:00 phase=seed only=['back_a'] key=llm=deepseek llm_calls=1 turns=0 probes=0
- run 2026-10-15T12:03:58+07:00 phase=seed only=['latex_b'] key=llm=deepseek llm_calls=1 turns=0 probes=0
- run 2026-10-15T12:05:45+07:00 phase=seed only=['ice_c'] key=llm=deepseek llm_calls=1 turns=0 probes=0
- run 2026-10-15T12:07:34+07:00 phase=seed only=['knee_d'] key=llm=deepseek llm_calls=1 turns=0 probes=0
- run 2026-10-15T12:09:22+07:00 phase=seed only=['job_e'] key=llm=deepseek llm_calls=2 turns=0 probes=0
- run 2026-10-15T12:11:29+07:00 phase=seed only=['goal_f'] key=llm=deepseek llm_calls=1 turns=0 probes=0
- run 2026-10-15T12:13:16+07:00 phase=seed only=['long_k'] key=llm=deepseek llm_calls=3 turns=0 probes=0
- run 2026-10-15T12:19:03+07:00 phase=probes only=['back_a'] key=llm=deepseek llm_calls=3 turns=1 probes=1
- run 2026-10-15T12:20:51+07:00 phase=probes only=['latex_b'] key=llm=deepseek llm_calls=3 turns=2 probes=2
- run 2026-10-15T12:22:39+07:00 phase=probes only=['ice_c'] key=llm=deepseek llm_calls=3 turns=3 probes=3
- run 2026-10-15T12:24:25+07:00 phase=probes only=['knee_d'] key=llm=deepseek llm_calls=3 turns=4 probes=4
- run 2026-10-15T12:26:07+07:00 phase=probes only=['job_e'] key=llm=deepseek llm_calls=3 turns=5 probes=5
- run 2026-10-15T12:27:49+07:00 phase=probes only=['goal_f'] key=llm=deepseek llm_calls=2 turns=6 probes=6
- run 2026-10-15T12:29:24+07:00 phase=probes only=['long_k'] key=llm=deepseek llm_calls=0 turns=6 probes=6
- run 2026-10-15T12:30:45+07:00 phase=all only=all key=llm=deepseek llm_calls=0 turns=6 probes=6

6/6 probes passed (0 invalid, không chấm).

## Pass rate by group

| group | passed/total | invalid | rate |
|---|---|---|---|
| a | 1/1 | 0 | 1.0 |
| b | 1/1 | 0 | 1.0 |
| c | 1/1 | 0 | 1.0 |
| d | 1/1 | 0 | 1.0 |
| e | 1/1 | 0 | 1.0 |
| f | 1/1 | 0 | 1.0 |

## Memory node (ms)

- n=6, p50=6518, p95=6546

## Retrieval similarity (b/e vs f)

- b/e top: 0.8581 values=[0.8581]
- f values=[]

## Background cost

- per turn: 0.0 LLM calls, 0.0 tokens
- avg turn total_tokens: 1341.0
- per 1,000 turns: {'background_tokens': 0, 'turn_tokens': 1341000}

## back_a

### [a] PASS — Nhắc lại xem tôi bị đau ở đâu?

- where: new, session: 6b6837a5-f990-5da9-82bd-48e42dce857d, attempt: 1, expect: ['lung'], forbid: []
- hit: ['lung'], leak: [], error: None, invalid: [], answer_source: stream, request_id: e9edb25f-49cf-470a-b25a-a8df5bfbb9b5
- retrieval: memory_search=0.8474, memory_ms=6518, to_done=25.47s, housekeep=0.0s, total_tokens=1130, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Theo bạn chia sẻ trước đó, bạn bị đau vùng lưng dưới mỗi khi ngồi lâu.

## latex_b

### [b] PASS — Gợi ý vài bài tập với dây kháng lực cho tôi?

- where: new, session: 6d193e47-fa84-5ceb-890f-b2eac9f590af, attempt: 1, expect: ['latex'], forbid: []
- hit: ['latex'], leak: [], error: None, invalid: [], answer_source: stream, request_id: 29f9369c-b49b-486b-be5f-21144f2d29ea
- retrieval: memory_search=None, memory_ms=6512, to_done=26.62s, housekeep=0.0s, total_tokens=2331, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Bạn dị ứng latex, nên mình loại ngay tất cả bài dùng dây kháng lực thông thường trong thư viện. Không dùng dây nào làm từ latex.

Thay vào đó, mình có một bài dùng tạ đơn, không chạm vào dây:

**Band Resisted Dumbbell Row**
- Nhắm vào cơ lưng (Lats)
- Thiết bị: Tạ đơn
- Độ khó: Trung cấp

**Chống chỉ định chung cho bài tập sức mạnh này:**
- Đau cấp tính vùng lưng hoặc vai.
- Chấn thương chưa lành ở khớp vai hoặc cột sống.
- **Nếu có bất kỳ triệu chứng bất thường nào, hãy dừng lại và gặp bác sĩ.**

Bạn thích tập phần lưng, hay muốn chuyển sang nhóm cơ khác như ngực hay vai? Mình tìm bài không dùng dây cho bạn.

*Nguồn: thư viện bài tập của ECA — Band Resisted Dumbbell Row.*

*Đây là gợi ý, không thay thế khám lâm sàng.*

## ice_c

### [c] PASS — Hôm trước bạn dặn tôi gì về vụ chườm đá?

- where: new, session: 10eba318-ae30-5d32-b785-3a0461b94183, attempt: 1, expect: ['chuom', 'da'], forbid: []
- hit: ['chuom', 'da'], leak: [], error: None, invalid: [], answer_source: stream, request_id: b96524d5-c9b4-4711-921b-651c38f29d24
- retrieval: memory_search=0.859, memory_ms=6546, to_done=25.62s, housekeep=0.0s, total_tokens=1174, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Mình không nhớ được nội dung chat cũ đâu. Nhưng quy tắc chung là chườm đá khoảng 15 đến 20 phút, cách vài giờ một lần, và tuyệt đối không áp đá trực tiếp lên da. Bạn nhớ đúng bao nhiêu phút không?

## knee_d

### [d] PASS — When did I have my left knee surgery?

- where: new, session: 3267c002-e66a-5511-9061-ba4cb54c8a9f, attempt: 1, expect: ['may', '2025'], forbid: []
- hit: ['may', '2025'], leak: [], error: None, invalid: [], answer_source: stream, request_id: 708c0f96-6cf6-46cf-9cb1-7c41622d7dcb
- retrieval: memory_search=0.8261, memory_ms=6432, to_done=21.93s, housekeep=0.0s, total_tokens=1118, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: May 2025. Meniscus repair on the left knee. You mentioned that before.

Is something bugging you about the leg today, or just checking?

## job_e

### [e] PASS — Hiện giờ tôi làm nghề gì?

- where: last, session: 21a9a672-b8ec-5b07-a45e-2a5b9cfbaf2d, attempt: 1, expect: ['giao vien'], forbid: ['y ta']
- hit: ['giao vien'], leak: [], error: None, invalid: [], answer_source: stream, request_id: 56086620-dec7-437d-becb-9defbf408f9c
- retrieval: memory_search=0.8581, memory_ms=6344, to_done=22.27s, housekeep=0.0s, total_tokens=1160, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Bạn đang làm giáo viên. Đúng không?

## goal_f

### [f] PASS — Thủ đô của Pháp là thành phố nào?

- where: new, session: 447e298a-17a0-5b11-8e9a-697d73047035, attempt: 1, expect: [], forbid: ['chay', '5km', 'xuan']
- hit: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: cffb4867-6a85-4e50-a653-6b726528cb18
- retrieval: memory_search=None, memory_ms=6546, to_done=14.62s, housekeep=0.0s, total_tokens=1133, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Paris. Bạn đang định du lịch sang đó, hay chỉ đơn giản là tò mò?

## long_k

- summary_check (legacy): PASS, compactions=3, kept=['vai', 'ngan']/['vai', 'ngan'], chars=1650
- excerpt: Người dùng yêu cầu trả lời ngắn gọn. Báo cáo vai phải đau khi giơ cao (tránh động tác quá đầu). Đã tư vấn: kỹ thuật heel-slide (10 lần x 2 lần/ngày), nâng chân thẳng (10 lần x 2-3 đợt), đi bộ (15-20 phút), xử lý bong gân cổ chân (RICE), giãn cơ cổ văn phòng, dinh dưỡng sau mổ gối, giấc ngủ, nâng bắp chân đứng (12 lần x 3 đợt), nghỉ ngơi khi đau gân kheo, tư thế ngồi, và plank (gối chạm sàn 20 giây x 3 lần). Người dùng chưa xác nhận đồng ý hay từ chối cụ thể, chỉ hỏi thêm chi tiết. Cần tiếp tục trả lời ngắn gọn, tránh động tác gây đau vai, và theo dõi tiến triển các bài tập đã nêu.

Người dùng hỏi về khởi động đá bóng, uống nước mùa nóng, đạp xe, bơi lội, giãn đùi sau, tập tạ tay và leo cầu thang cho người đau gối. Đã tư vấn chi tiết: khởi động 12-15 phút; uống 400-500ml trước tập, bù 1,2 l

