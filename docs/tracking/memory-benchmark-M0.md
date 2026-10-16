# memory-benchmark-M0

Model: qwen/qwen3.8-27b via Groq. A và B cùng chạy trên model này nên so sánh hai thiết kế memory là công bằng, nhưng chất lượng, độ trễ và token KHÔNG đại diện cho prod (prod dùng DeepSeek trực tiếp).

- run 2026-10-16T09:02:13+07:00 phase=seed only=['m_name'] key=llm=groq-qwen3.8-27b llm_calls=1 turns=0 probes=0
- run 2026-10-16T09:04:14+07:00 phase=seed only=['m_food'] key=llm=groq-qwen3.8-27b llm_calls=1 turns=0 probes=0
- run 2026-10-16T09:06:12+07:00 phase=seed only=['m_ambig'] key=llm=groq-qwen3.8-27b llm_calls=2 turns=0 probes=0
- run 2026-10-16T09:08:16+07:00 phase=probes only=['m_name'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=1 probes=1
- run 2026-10-16T09:10:03+07:00 phase=probes only=['m_food'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=2 probes=2
- run 2026-10-16T09:12:14+07:00 phase=probes only=['m_ambig'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=3 probes=3

1/3 probes passed (0 invalid, không chấm).

## Pass rate by group

| group | passed/total | invalid | rate |
|---|---|---|---|
| m | 1/3 | 0 | 0.333 |

## Memory node (ms)

- n=3, p50=6890, p95=7018

## Retrieval similarity (b/e vs f)

- b/e top: None values=[]
- f values=[]

## Background cost

- per turn: 0.0 LLM calls, 0.0 tokens
- avg turn total_tokens: 2412.0
- per 1,000 turns: {'background_tokens': 0, 'turn_tokens': 2412000}

## m_name

### [m] PASS — who am I? tôi bị đau lưng, gợi ý vài bài tập giúp tôi

- where: new, session: e460e634-858e-5efa-bc8d-f87289cdf8b8, attempt: 1, expect: [], expect_all: [['mon'], ['lung']], expect_tools: ['kb_search'], forbid: [], forbid_mode: clarify
- hit: [], all_hit: ['mon', 'lung'], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: 9c22f7d3-55ab-4f9e-8461-a9632b3d7715
- mode: synthesize, tools: ['kb_search', 'resume_last_session'], tools_missing: [], mode_hit: False, retrieval: memory_search=None, memory_ms=7018, to_done=20.55s, housekeep=0.0s, total_tokens=2718, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Mon của mình đây. Nhưng mình là AI, không phải người thật.

Về đau lưng, NHS khuyên bạn nên tiếp tục hoạt động nhẹ, không nằm bất động (NHS). Nếu đau tăng, dừng ngay và gặp bác sĩ (NHS).

Mình gợi ý 2 bài trong thư viện ECA:
1. **Back extension**: Nằm trên ghế 45 độ, chân cố định. Gập người xuống rồi nâng lên. Không tạ: 15-20 lần mỗi hiệp (ECA).
2. **TBS Back Extension**: Tương tự, tập trung vào lưng dưới, cơ mông và gân kheo (ECA).

**Chống chỉ định (Dừng ngay và đi khám nếu có):**
- Đau, tê, yếu hoặc mất cảm giác ở cả hai chân.
- Mất cảm giác quanh vùng sinh dục hoặc hậu môn.
- Thay đổi chức năng bàng quang hoặc ruột (khó tiểu, vô tình tiểu/đại tiện).
- Đau ngực, hoặc đau sau tai nạn nghiêm trọng (NHS).

Nếu bạn có các dấu hiệu này, **gọi 115 hoặc đến bệnh viện ngay**. Không tự lái xe.

Bạn đang đau ở vùng nào và đau từ khi nào?

*Nguồn: thư viện bài tập của ECA — Back extension, TBS Back Extension; hướng dẫn sức khỏe của NHS.*

*Đây là gợi ý, không thay thế khám lâm sàng.*

## m_food

### [m] FAIL — Món ăn yêu thích của tôi là gì? Với lại gối tôi đau khi lên cầu thang thì nên tập gì?

- where: new, session: 00ecae23-ce60-584d-8a19-a1bcb40bed16, attempt: 1, expect: [], expect_all: [['pho'], ['goi']], expect_tools: ['kb_search'], forbid: [], forbid_mode: None
- hit: [], all_hit: ['goi'], all_miss: [['pho']], leak: [], error: None, invalid: [], answer_source: stream, request_id: 058d04d5-03cc-462f-9879-8ac692b6162a
- mode: synthesize, tools: ['recall_self', 'kb_search'], tools_missing: [], mode_hit: False, retrieval: memory_search=None, memory_ms=6840, to_done=17.63s, housekeep=0.0s, total_tokens=2632, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Mình không biết món ăn yêu thích của bạn là gì. Bạn chưa từng kể cho mình nghe.

Về việc đau gối khi lên cầu thang: mình không phải bác sĩ, không chẩn đoán được nguyên nhân. Bạn cần gặp chuyên gia y tế để kiểm tra kỹ.

Nếu bạn vẫn muốn tập để hỗ trợ cơ bắp đùi và mông (theo hướng dẫn của NHS và thư viện ECA), đây là bài tập nhẹ:

**Mini-squats (NHS)**
*   **Cách làm:**
    1.  Đứng chân rộng bằng hông, tay tựa vào lưng ghế vững chắc, không có bánh xe.
    2.  Cong gối từ từ xuống mức thoải mái, giữ lưng thẳng, hướng gối về phía trước (quá ngón chân cái).
    3.  Đứng dậy, siết mông.
*   **Lượng:** Lặp lại 5 lần.
*   **Dừng ngay nếu:** Cảm thấy đau tăng, mất thăng bằng hoặc gối bị "lỏng".

**Hip marching (NHS)**
*   **Cách làm:**
    1.  Ngồi thẳng, không tựa lưng ghế, nắm mép ghế.
    2.  Nhấc chân trái lên với gối cong ở mức thoải mái, đặt chân xuống có kiểm soát.
    3.  Đổi chân phải.
*   **Lượng:** 5 lần mỗi chân.
*   **Dừng ngay nếu:** Đau lan ra hoặc mất thăng bằng.

**Contraindication (Chống chỉ định):**
*   Không tập nếu bạn có chấn thương gối cấp tính, viêm khớp nặng đang bùng phát, hoặc bác sĩ đã cấm vận động.
*   **Luôn dừng lại ngay lập tức nếu:** Đau tăng lên, sưng nóng, hoặc cảm giác gối bị kẹt/không ổn định.

Mẹo từ NHS: Chọn ghế vững, quần áo thoải mái, uống nước. Tập 2 lần mỗi tuần, tăng dần từ từ.

Hãy đi khám trước khi tập thêm.

*Nguồn: hướng dẫn sức khỏe của NHS.*

*Đây là gợi ý, không thay thế khám lâm sàng.*

## m_ambig

### [m] FAIL — hôm trước mình nói gì nhỉ? và cho tôi bài giãn cơ cổ

- where: new, session: 9fe85d6c-24f9-52bb-b646-c70f172b5ea2, attempt: 1, expect: [], expect_all: [['co']], expect_tools: [], forbid: [], forbid_mode: clarify
- hit: [], all_hit: ['co'], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: 258fee5d-86f0-4bfb-8cd7-b0bd205dd36b
- mode: clarify, tools: ['resume_last_session', 'kb_search'], tools_missing: [], mode_hit: True, retrieval: memory_search=None, memory_ms=6890, to_done=22.77s, housekeep=0.0s, total_tokens=1886, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Mình nhớ hai cuộc trò chuyện gần đây: một về bài tập lưng và một về giãn vai. Bạn muốn nhắc lại cái nào?

Để chọn bài giãn cổ phù hợp, mình cần biết: bạn đang bị căng cứng hay có cảm giác đau nhói, tê tay?

*Đây là gợi ý, không thay thế khám lâm sàng.*

