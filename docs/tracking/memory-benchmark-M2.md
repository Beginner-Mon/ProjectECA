# memory-benchmark-M2

Model: qwen/qwen3.8-27b via Groq. A và B cùng chạy trên model này nên so sánh hai thiết kế memory là công bằng, nhưng chất lượng, độ trễ và token KHÔNG đại diện cho prod (prod dùng DeepSeek trực tiếp).

- run 2026-10-16T10:53:59+07:00 phase=seed only=['m_name'] key=llm=groq-qwen3.8-27b llm_calls=1 turns=0 probes=0
- run 2026-10-16T10:56:04+07:00 phase=seed only=['m_food'] key=llm=groq-qwen3.8-27b llm_calls=1 turns=0 probes=0
- run 2026-10-16T10:58:06+07:00 phase=seed only=['m_ambig'] key=llm=groq-qwen3.8-27b llm_calls=2 turns=0 probes=0
- run 2026-10-16T11:00:08+07:00 phase=probes only=['m_name'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=1 probes=1
- run 2026-10-16T11:02:04+07:00 phase=probes only=['m_food'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=2 probes=2
- run 2026-10-16T11:03:56+07:00 phase=probes only=['m_ambig'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=3 probes=3

3/3 probes passed (0 invalid, không chấm).

## Pass rate by group

| group | passed/total | invalid | rate |
|---|---|---|---|
| m | 3/3 | 0 | 1.0 |

## Memory node (ms)

- n=3, p50=6214, p95=6330

## Retrieval similarity (b/e vs f)

- b/e top: None values=[]
- f values=[]

## Background cost

- per turn: 0.0 LLM calls, 0.0 tokens
- avg turn total_tokens: 2466.3
- per 1,000 turns: {'background_tokens': 0, 'turn_tokens': 2466333}

## m_name

### [m] PASS — who am I? tôi bị đau lưng, gợi ý vài bài tập giúp tôi

- where: new, session: acbe2ecc-0eeb-58b7-8df2-7751fd9bcc8a, attempt: 1, expect: [], expect_all: [['mon'], ['lung']], expect_tools: ['kb_search'], forbid: [], forbid_mode: clarify
- hit: [], all_hit: ['mon', 'lung'], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: 1fa125d7-ecff-4148-9b2c-6966199e998f
- mode: synthesize, tools: ['kb_search', 'resume_last_session'], tools_missing: [], mode_hit: False, retrieval: tier2=0.8464069366455078, memory_ms=6330, to_done=20.98s, housekeep=0.0s, total_tokens=2705, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=0.8464069366455078, skipped=None, embed_ms=63, user_facts=0, user_instructions=0
- answer: Bạn là Mon. Nhớ bạn thích ăn phở.

Đau lưng? Ngồi lên ngay, đừng nằm dài. Thư viện ECA có bài **Back extension** (kéo giãn lưng dưới) cho bạn.

Cách làm:
1. Ngồi lên ghế 45 độ, chân cố định.
2. Gồng lưng dưới, nâng thân người lên.
3. Hạ xuống chậm.
4. Làm 15-20 lần không tạ, hoặc 8-12 lần nếu thêm tạ.

**CẢNH BÁO: DỪNG NGAY nếu đau tăng lên, tê lan xuống chân, hoặc mất kiểm soát bàng quang/trực tràng. Những triệu chứng này cần đi khám bác sĩ ngay.**

**Chống chỉ định:**
- Đau lưng cấp tính nặng.
- Có vấn đề về đĩa đệm chưa được chẩn đoán.
- Đau lan xuống chân (tê, yếu).
- Vừa chấn thương nặng (tai nạn, ngã).

Nếu đau kéo dài vài tuần không hết, hoặc đau dữ dội, đi khám bác sĩ hoặc vật lý trị liệu. Đừng tự đoán nguyên nhân.

Bạn có thể làm bài này ngay bây giờ không?

*Nguồn: thư viện bài tập của ECA — Back extension.*

*Đây là gợi ý, không thay thế khám lâm sàng.*

## m_food

### [m] PASS — Món ăn yêu thích của tôi là gì? Với lại gối tôi đau khi lên cầu thang thì nên tập gì?

- where: new, session: 4d719b85-6823-5623-8175-72036b7ab1b4, attempt: 1, expect: [], expect_all: [['pho'], ['goi']], expect_tools: ['kb_search'], forbid: [], forbid_mode: None
- hit: [], all_hit: ['pho', 'goi'], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: ca1a385a-bda8-4e6a-9f3e-7ef82103b8e8
- mode: synthesize, tools: ['recall_self', 'kb_search'], tools_missing: [], mode_hit: False, retrieval: tier2=0.9068240738055776, memory_ms=6214, to_done=17.27s, housekeep=0.0s, total_tokens=2517, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=0.9068240738055776, skipped=None, embed_ms=58, user_facts=1, user_instructions=1
- answer: Phở. Bạn nói từ trước, mình nhớ.

Gối đau khi leo cầu thang thì cần tăng sức mạnh cơ đùi trước và mông. Thư viện ECA có bài **Step-up with knee raise** (Intermediate, Body Only) nhắm đúng nhóm cơ này. Nguồn NHS cũng gợi ý **Mini-squats** để hỗ trợ di động.

Cách tập Mini-squats (NHS):
1. Đứng chân rộng bằng hông, tay tì vào lưng ghế để giữ thăng bằng.
2. Gập gối từ từ xuống mức thoải mái, mũi chân phải luôn thấy được, lưng thẳng.
3. Đứng dậy, siết mông.
4. Lặp lại 5 lần.

**Chống chỉ định (Contraindication):**
- Đau cấp tính, sưng tấy hoặc nóng đỏ tại khớp gối.
- Gãy xương hoặc chấn thương mô mềm chưa lành.
- Nếu tập thấy đau tăng lên bất thường, **ngừng ngay**.

Mình không phải bác sĩ. Nếu đau kéo dài hoặc nặng dần, hãy đi khám.

*Nguồn: thư viện bài tập của ECA — Step-up with knee raise; hướng dẫn sức khỏe của NHS.*

*Đây là gợi ý, không thay thế khám lâm sàng.*

## m_ambig

### [m] PASS — hôm trước mình nói gì nhỉ? và cho tôi bài giãn cơ cổ

- where: new, session: 707452e6-767c-5bdf-9f60-435e2d88473f, attempt: 1, expect: [], expect_all: [['co']], expect_tools: [], forbid: [], forbid_mode: clarify
- hit: [], all_hit: ['co'], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: 721e6cf3-f888-4a99-8aaf-a7f0603294a8
- mode: synthesize, tools: ['resume_last_session', 'kb_search'], tools_missing: [], mode_hit: False, retrieval: memory_search=None, memory_ms=6194, to_done=22.66s, housekeep=0.0s, total_tokens=2177, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- tier2: top_sim=None, skipped=None, embed_ms=51, user_facts=0, user_instructions=0
- answer: Trước đó bạn hỏi bài tập lưng, mình đã gửi cat-cow 10 lần và bird-dog mỗi bên 8 lần.

Giờ đến phần cổ. Dưới đây là các bài giãn cơ cổ từ nguồn NHS:

**1. Giãn cổ nghiêng (Neck stretch)**
- Ngồi thẳng, nhìn thẳng về phía trước.
- Dùng tay phải giữ vai trái xuống.
- Nghiêng đầu từ từ sang phải. Giữ nguyên 5 giây.
- Lặp lại 3 lần mỗi bên.

**2. Xoay cổ (Neck rotation)**
- Ngồi thẳng, vai buông lỏng, nhìn thẳng.
- Xoay đầu từ từ sang trái đến mức thoải mái. Giữ 5 giây rồi về vị trí ban đầu.
- Lặp lại sang phải.
- Thực hiện 3 lần xoay mỗi bên.

Thư viện ECA còn có bài **Neck-SMR** (giãn cơ, độ khó trung bình, không cần dụng cụ).

**Chống chỉ định:**
- **Nếu bạn có chấn thương cổ cấp tính, đau dữ dội kèm tê bì tay, hoặc yếu cơ tay, hãy NGƯNG ngay.**
- **Không gồng cổ quá mức. Dừng lại ngay khi cảm thấy khó chịu hoặc đau tăng.**

Bạn làm thử 1 set xem sao nhé.

*Nguồn: thư viện bài tập của ECA — Neck-SMR; hướng dẫn sức khỏe của NHS.*

*Đây là gợi ý, không thay thế khám lâm sàng.*

