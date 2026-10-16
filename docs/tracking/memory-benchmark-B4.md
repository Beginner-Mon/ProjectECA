# memory-benchmark-B4

Model: qwen/qwen3.8-27b via Groq. A và B cùng chạy trên model này nên so sánh hai thiết kế memory là công bằng, nhưng chất lượng, độ trễ và token KHÔNG đại diện cho prod (prod dùng DeepSeek trực tiếp).

- run 2026-10-16T16:49:59+07:00 phase=seed only=['back_a'] key=llm=deepseek llm_calls=1 turns=0 probes=0
- run 2026-10-16T16:51:36+07:00 phase=probes only=['back_a'] key=llm=deepseek llm_calls=3 turns=1 probes=1
- run 2026-10-16T16:53:18+07:00 phase=seed only=['latex_b'] key=llm=deepseek llm_calls=1 turns=1 probes=1
- run 2026-10-16T16:54:56+07:00 phase=probes only=['latex_b'] key=llm=deepseek llm_calls=3 turns=2 probes=2
- run 2026-10-16T16:55:46+07:00 phase=seed only=['ice_c'] key=llm=deepseek llm_calls=1 turns=2 probes=2
- run 2026-10-16T16:57:23+07:00 phase=probes only=['ice_c'] key=llm=deepseek llm_calls=3 turns=3 probes=3
- run 2026-10-16T16:59:18+07:00 phase=seed only=['knee_d'] key=llm=deepseek llm_calls=0 turns=3 probes=3
- run 2026-10-16T17:00:54+07:00 phase=probes only=['knee_d'] key=llm=deepseek llm_calls=0 turns=4 probes=4
- run 2026-10-16T17:02:38+07:00 phase=seed only=['job_e'] key=llm=deepseek llm_calls=0 turns=4 probes=4
- run 2026-10-16T17:04:36+07:00 phase=probes only=['job_e'] key=llm=deepseek llm_calls=1 turns=5 probes=5
- run 2026-10-16T17:29:30+07:00 phase=seed only=['knee_d'] key=llm=deepseek llm_calls=1 turns=4 probes=4
- run 2026-10-16T17:30:16+07:00 phase=probes only=['knee_d'] key=llm=deepseek llm_calls=1 turns=5 probes=5
- run 2026-10-16T17:31:55+07:00 phase=seed only=['job_e'] key=llm=deepseek llm_calls=0 turns=4 probes=4
- run 2026-10-16T17:33:52+07:00 phase=probes only=['job_e'] key=llm=deepseek llm_calls=3 turns=5 probes=5

2/2 probes passed (3 invalid, không chấm).

## Pass rate by group

| group | passed/total | invalid | rate |
|---|---|---|---|
| a | 1/1 | 0 | 1.0 |
| b | 1/1 | 0 | 1.0 |
| c | 0/1 | 1 | 0.0 |
| d | 0/1 | 1 | 0.0 |
| e | 0/1 | 1 | 0.0 |

## Memory node (ms)

- n=2, p50=6248, p95=6248

## Retrieval similarity (b/e vs f)

- b/e top: None values=[]
- f values=[]

## Background cost

- per turn: 0.0 LLM calls, 0.0 tokens
- avg turn total_tokens: 1515.8
- per 1,000 turns: {'background_tokens': 0, 'turn_tokens': 1515800}

## back_a

### [a] PASS — Nhắc lại xem tôi bị đau ở đâu?

- where: new, session: f6e9450d-683a-57fa-b082-524cca156b67, attempt: 1, expect: ['lung'], expect_all: [], expect_tools: [], forbid: [], forbid_mode: None
- hit: ['lung'], all_hit: [], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: 2da265ad-c595-45cb-8d4a-d67f35860e9c
- mode: chat, tools: ['recall_self'], tools_missing: [], mode_hit: False, retrieval: memory_search=None, memory_ms=6248, to_done=15.9s, housekeep=0.0s, total_tokens=1297, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=None, skipped=None, embed_ms=69, user_facts=0, user_instructions=1
- answer: Bạn nhắc mình đau lưng dưới mỗi khi ngồi lâu. Đó là thông tin bạn đã chia sẻ trước đó. Hôm nay tình hình đỡ hơn chưa?

## latex_b

### [b] PASS — Gợi ý vài bài tập với dây kháng lực cho tôi?

- where: new, session: 3e54a865-a33f-5db9-98d2-6b78a5f229ef, attempt: 1, expect: ['latex'], expect_all: [], expect_tools: [], forbid: [], forbid_mode: None
- hit: ['latex'], all_hit: [], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: fd664cad-3a92-4b3f-a293-190c93ba6604
- mode: synthesize, tools: ['kb_search'], tools_missing: [], mode_hit: False, retrieval: memory_search=None, memory_ms=6177, to_done=16.75s, housekeep=0.0s, total_tokens=2446, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=None, skipped=None, embed_ms=67, user_facts=0, user_instructions=2
- answer: Mình chọn 3 bài từ thư viện ECA cho bạn tập ngay.

**1. Band lateral raise (Vai)**
- Mức độ: Trung bình.
- Mục tiêu: Cơ delta giữa.
- Liều lượng: 8-12 reps mỗi set (theo nguồn ECA).
- Điểm dừng: Đau khớp vai, tê bì hoặc mất thăng bằng.

**2. Band overhead triceps extension (Tam đầu)**
- Mức độ: Trung bình.
- Mục tiêu: Đầu dài cơ tam đầu.
- Liều lượng: 8-12 reps trở lên mỗi set (theo nguồn ECA).
- Điểm dừng: Đau khuỷu hoặc vai.

**3. Band push-up (Ngực)**
- Mức độ: Trung bình.
- Mục tiêu: Ngực, tam đầu, vai, lưng trên và lõi.
- Điểm dừng: Đau cổ tay, vai hoặc lưng dưới.

**Contraindication (Chống chỉ định):**
- Dị ứng latex: Bạn ghi chú dị ứng latex. **Tuyệt đối không dùng dây kháng lực làm từ latex.** Hãy kiểm tra kỹ chất liệu trước khi tập.
- Đau cấp tính tại vùng vai, khuỷu hoặc cổ tay: Không tập các bài tác động trực tiếp vào vùng đó.
- Nếu xuất hiện đau dữ dội, tê liệt hoặc mất kiểm soát chi trong khi tập: **Dừng tập ngay lập tức và đi khám bác sĩ.**

Bạn cầm dây tập một set bài nào trước?

*Nguồn: thư viện bài tập của ECA — Band overhead triceps extension, Band push-up, Band lateral raise.*

*Đây là gợi ý, không thay thế khám lâm sàng.*

## ice_c

### [c] INVALID — Hôm trước bạn dặn tôi gì về vụ chườm đá?

- where: new, session: 8260161e-9d3c-51d4-9e4e-f4ea656beee4, attempt: 1, expect: ['chuom', 'da'], expect_all: [], expect_tools: [], forbid: [], forbid_mode: None
- hit: [], all_hit: [], all_miss: [], leak: [], error: None, invalid: ['provider_429', 'llm_fallback_used'], answer_source: stream, request_id: d85de8d0-1dfa-4ef3-a293-7c5cf2b57dea
- mode: None, tools: [], tools_missing: [], mode_hit: False, retrieval: memory_search=None, memory_ms=6111, to_done=20.79s, housekeep=0.0s, total_tokens=2311, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=None, skipped=None, embed_ms=42, user_facts=0, user_instructions=0
- answer: Mình không tìm thấy thông tin nào về việc chườm đá trong thư viện bài tập của ECA bạn nhé.

*Đây là gợi ý, không thay thế khám lâm sàng.*

## knee_d

### [d] INVALID — When did I have my left knee surgery?

- where: new, session: 2482b878-0f9a-5d0e-8626-5a9d8b7d08ee, attempt: 1, expect: ['may', '2025'], expect_all: [], expect_tools: [], forbid: [], forbid_mode: None
- hit: [], all_hit: [], all_miss: [], leak: [], error: None, invalid: ['provider_429', 'node_failed:retriever_agent'], answer_source: stream, request_id: 77a96e61-feb6-41fa-8c3c-ec75b73b5f62
- mode: None, tools: [], tools_missing: [], mode_hit: False, retrieval: memory_search=None, memory_ms=6240, to_done=13.26s, housekeep=0.0s, total_tokens=0, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=None, skipped=None, embed_ms=78, user_facts=0, user_instructions=1
- answer: 

## job_e

### [e] INVALID — Hiện giờ tôi làm nghề gì?

- where: last, session: 60375709-78e6-56e7-8e1f-3430e41c1f86, attempt: 1, expect: ['giao vien'], expect_all: [], expect_tools: [], forbid: ['y ta'], forbid_mode: None
- hit: [], all_hit: [], all_miss: [], leak: [], error: None, invalid: ['provider_429', 'llm_fallback_used'], answer_source: stream, request_id: d6d75c71-cc7c-4676-a4e8-756148ef3483
- mode: None, tools: [], tools_missing: [], mode_hit: False, retrieval: memory_search=None, memory_ms=6146, to_done=20.33s, housekeep=0.0s, total_tokens=1525, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=None, skipped=None, embed_ms=51, user_facts=0, user_instructions=0
- answer: Mình nhớ rồi, bạn là giáo viên.

