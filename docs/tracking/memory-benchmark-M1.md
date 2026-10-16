# memory-benchmark-M1

Model: qwen/qwen3.8-27b via Groq. A và B cùng chạy trên model này nên so sánh hai thiết kế memory là công bằng, nhưng chất lượng, độ trễ và token KHÔNG đại diện cho prod (prod dùng DeepSeek trực tiếp).

- run 2026-10-16T09:33:51+07:00 phase=seed only=['m_name'] key=llm=groq-qwen3.8-27b llm_calls=1 turns=0 probes=0
- run 2026-10-16T09:35:57+07:00 phase=seed only=['m_food'] key=llm=groq-qwen3.8-27b llm_calls=1 turns=0 probes=0
- run 2026-10-16T09:38:08+07:00 phase=seed only=['m_ambig'] key=llm=groq-qwen3.8-27b llm_calls=2 turns=0 probes=0
- run 2026-10-16T09:40:09+07:00 phase=probes only=['m_name'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=1 probes=1
- run 2026-10-16T09:41:57+07:00 phase=probes only=['m_food'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=2 probes=2
- run 2026-10-16T09:44:03+07:00 phase=probes only=['m_ambig'] key=llm=groq-qwen3.8-27b llm_calls=3 turns=3 probes=3

2/3 probes passed (0 invalid, không chấm).

## Pass rate by group

| group | passed/total | invalid | rate |
|---|---|---|---|
| m | 2/3 | 0 | 0.667 |

## Memory node (ms)

- n=3, p50=6924, p95=7020

## Retrieval similarity (b/e vs f)

- b/e top: None values=[]
- f values=[]

## Background cost

- per turn: 0.0 LLM calls, 0.0 tokens
- avg turn total_tokens: 2591.3
- per 1,000 turns: {'background_tokens': 0, 'turn_tokens': 2591333}

## m_name

### [m] PASS — who am I? tôi bị đau lưng, gợi ý vài bài tập giúp tôi

- where: new, session: 3d2aa860-521a-5fc6-ba17-6b446011ca6d, attempt: 1, expect: [], expect_all: [['mon'], ['lung']], expect_tools: ['kb_search'], forbid: [], forbid_mode: clarify
- hit: [], all_hit: ['mon', 'lung'], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: d737eff8-0020-4776-9e3b-51b4e7f71d06
- mode: synthesize, tools: ['kb_search', 'resume_last_session'], tools_missing: [], mode_hit: False, retrieval: memory_search=None, memory_ms=6910, to_done=21.24s, housekeep=0.0s, total_tokens=2943, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Bạn là Mon. Đau lưng thì mình có vài gợi ý trong thư viện ECA, nhưng trước hết cần loại trừ các dấu hiệu nguy hiểm.

**Nếu bạn có bất kỳ triệu chứng nào sau đây, hãy gọi 999 hoặc đến cấp cứu ngay (NHS):**
*   Đau, tê, yếu hoặc mất cảm giác ở cả hai chân
*   Mất cảm giác quanh vùng sinh dục hoặc hậu môn
*   Thay đổi chức năng bàng quang hoặc ruột (khó đi tiểu, tiểu không kiểm soát)
*   Đau ngực
*   Đau bắt đầu sau tai nạn nghiêm trọng

Nếu không có các dấu hiệu trên, đây là các bài tập trong thư viện ECA cho vùng lưng dưới:

**1. Back Extension (và các biến thể TBS Back Extension, Hyperextension - Gethin Variation)**
*   **Mô tả:** Tập sức mạnh cho lưng dưới, cơ mông và hamstring. Thường thực hiện trên ghế nghiêng 45 độ, chân được cố định.
*   **Tần suất/Rep:**
    *   Không tạ: 15-20 reps hoặc nhiều hơn (ECA's exercise library).
    *   Có tạ: 8-12 reps mỗi set (ECA's exercise library).
*   **Lưu ý an toàn:** **Dừng lại ngay nếu cơn đau trở nên tồi tệ hơn và đi khám bác sĩ để được tư vấn (NHS).**

**Chống chỉ định (Contraindication):**
*   Không tập nếu bạn có các dấu hiệu nguy hiểm cấp tính đã liệt kê ở trên.
*   Nếu cơn đau lưng dữ dội hoặc không giảm sau vài tuần, cần gặp bác sĩ để được chỉ định phương pháp điều trị phù hợp như vật lý trị liệu, thủ thuật hoặc phẫu thuật (NHS).

Ngoài tập luyện, NHS khuyến cáo bạn nên:
*   Tiếp tục các hoạt động hàng ngày, tránh nằm bất động lâu.
*   Dùng túi đá chườm (bọc khăn) để giảm đau và sưng, hoặc chườm ấm để giảm co cứng cơ.
*   Thă

## m_food

### [m] FAIL — Món ăn yêu thích của tôi là gì? Với lại gối tôi đau khi lên cầu thang thì nên tập gì?

- where: new, session: ce1a39f9-8606-5ee1-b0c5-156f88975499, attempt: 1, expect: [], expect_all: [['pho'], ['goi']], expect_tools: ['kb_search'], forbid: [], forbid_mode: None
- hit: [], all_hit: ['goi'], all_miss: [['pho']], leak: [], error: None, invalid: [], answer_source: stream, request_id: 9da4e23d-7aa6-4598-9893-28ceba77ad36
- mode: synthesize, tools: ['recall_self', 'kb_search'], tools_missing: [], mode_hit: False, retrieval: memory_search=None, memory_ms=6924, to_done=17.49s, housekeep=0.0s, total_tokens=2578, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Mình không biết món ăn yêu thích của bạn là gì. Đây là thông tin cá nhân nên mình không nắm được.

Về việc gối đau khi leo cầu thang, thư viện ECA có bài **Step-up with knee raise** (Bước lên, nâng đầu gối). Bài này nhắm vào cơ đùi trước, mông và gân sau, giúp tăng lực mở hông và ổn định khớp gối.

**Cách thực hiện:**
1. Đứng trước một bậc cầu thang hoặc khối vững chắc.
2. Bước một chân lên, đẩy gối chân kia lên cao nhất có thể.
3. Hạ chân xuống một cách có kiểm soát.

**Lưu ý an toàn:**
- **Dừng ngay nếu thấy đau nhói hoặc khó chịu tăng lên ở vùng gối.**
- Giữ lưng thẳng và đầu gối hướng về phía trước.

Ngoài ra, trang NHS có gợi ý bài **Mini-squats** (Ngồi xổm nhẹ) để tăng sức mạnh cơ tứ đầu đùi, hỗ trợ khớp gối:
1. Đặt tay lên lưng ghế để giữ thăng bằng.
2. Chân mở rộng bằng hông, từ từ gập gối đến mức thoải mái.
3. Đứng dậy, siết chặt cơ mông.
4. Lặp lại 5 lần.

**Chống chỉ định:**
- **Không tập nếu bạn đang có chấn thương cấp tính, viêm khớp nặng hoặc bác sĩ đã hạn chế các động tác chịu lực lên gối.**
- **Nếu cơn đau lan xuống cẳng chân hoặc gây tê bì, hãy đi khám ngay thay vì tập luyện.**

Bạn nên bắt đầu với cường độ thấp và tăng dần. Nếu đau không giảm sau vài ngày, hãy gặp bác sĩ để được chẩn đoán chính xác.

*Nguồn: thư viện bài tập của ECA — Step-up with knee raise; hướng dẫn sức khỏe của NHS.*

*Đây là gợi ý, không thay thế khám lâm sàng.*

## m_ambig

### [m] PASS — hôm trước mình nói gì nhỉ? và cho tôi bài giãn cơ cổ

- where: new, session: 8a2b0509-042f-5ddc-b88e-9bd4de645370, attempt: 1, expect: [], expect_all: [['co']], expect_tools: [], forbid: [], forbid_mode: clarify
- hit: [], all_hit: ['co'], all_miss: [], leak: [], error: None, invalid: [], answer_source: stream, request_id: e8618963-51c6-43ea-8a2f-5f3b073256cf
- mode: synthesize, tools: ['resume_last_session', 'kb_search'], tools_missing: [], mode_hit: False, retrieval: memory_search=None, memory_ms=7020, to_done=23.83s, housekeep=0.0s, total_tokens=2253, bg={'llm_calls': 0, 'tokens': 0, 'elapsed_ms': 0}
- answer: Hôm trước bạn hỏi bài tập lưng, mình nhắc lại cat-cow 10 lần và bird-dog mỗi bên 8 lần.

Về giãn cơ cổ, thư viện ECA có bài **Neck-SMR** (cấp độ trung bình, chỉ dùng cơ thể). Dưới đây là 2 bài bổ trợ từ nguồn NHS (nhs.uk):

**1. Xoay cổ (Neck rotation) - NHS**
*   Ngồi thẳng, vai thả lỏng, nhìn thẳng.
*   Xoay đầu chậm rãi về phía vai trái đến mức thoải mái. Giữ 5 giây.
*   Về vị trí ban đầu. Lặp lại sang bên phải.
*   Làm 3 lần xoay mỗi bên.
*   **Dừng ngay nếu bạn thấy chóng mặt, tê bì tay hoặc đau nhói.**

**2. Kéo giãn cổ (Neck stretch) - NHS**
*   Ngồi thẳng, nhìn thẳng. Dùng tay phải giữ vai trái cố định xuống sàn.
*   Chậm rãi nghiêng đầu sang phải, giữ vai trái không nhấc lên.
*   Giữ 5 giây. Lặp lại sang bên kia.
*   Làm 3 lần mỗi bên.
*   **Dừng ngay nếu bạn cảm thấy đau dữ dội hoặc mất thăng bằng.**

**Chống chỉ định (Contraindications):**
Bạn **KHÔNG NÊN** thực hiện các bài tập này nếu bạn đang bị chấn thương cổ cấp tính, gãy xương, viêm khớp nặng hoặc có tiền sử trượt đốt sống cổ. Nếu có bất kỳ tình trạng nào trong số này, hãy tham vấn bác sĩ trước.

Bạn thử làm set xoay cổ trước nhé, nhẹ nhàng thôi.

*Nguồn: thư viện bài tập của ECA — Neck-SMR; hướng dẫn sức khỏe của NHS.*

*Đây là gợi ý, không thay thế khám lâm sàng.*

