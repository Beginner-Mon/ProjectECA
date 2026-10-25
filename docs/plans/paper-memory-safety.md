---
tags: [paper, safety, memory, guardrail, plan]
date: 2026-10-24
author: K, cho Tri và Nguyen
status: nháp, chờ smoke test S3
---

# Paper: memory chỉ được nâng mức cảnh báo (Escalate-Only Memory)

Phần sản phẩm (guardrail) nằm ở [[guardrail-feasibility]]. Bộ dữ liệu dùng lại từ
[[paper-safety-eval-h2]].

> **Cập nhật 25/10.** Kết quả S3, S3b và S3c nằm ở [[paper-memory-safety-results]].
> - Planner của ECA không bị memory kéo xuống, nên EOM ở tầng planner không có lỗi để sửa.
> - Trợ lý thường chạy DeepSeek thì bị kéo xuống rất mạnh.
> - Câu quy tắc an toàn trong prompt giữ được cảnh báo, nhưng gây 24 % báo động sai.
> - Kiểu ECA hoàn chỉnh chỉ chống được một phần.
>
> Plan dưới đây là bản trước khi có số liệu.

## 1. Phương pháp dùng để viết paper

**Bài toán.** Trợ lý sức khỏe nhận tin nhắn x và có memory M của người dùng (fact, lời dặn cố
định, tóm tắt). Nó phải quyết định mức khẩn s, từ L0 (bình thường) đến L3 (cấp cứu). Memory tác
động lên s theo hai chiều ngược nhau:

- **Chiều có lợi:** memory là bằng chứng lâm sàng (tai nạn tuần trước, bệnh nền, tuổi, thuốc), nên
  s phải tăng.
- **Chiều có hại:** lời dặn ("đừng khuyên tôi đi khám"), mục tiêu ("sắp thi chạy"), và chẩn đoán
  cũ gây neo ("bác sĩ bảo thoát vị nhẹ, cứ tập") kéo s xuống.

**Phương pháp: Escalate-Only Memory (EOM).**

1. Chia memory theo loại:
   - M_ev là bằng chứng lâm sàng: bệnh, hồ sơ, sự kiện như tai nạn, thuốc.
   - M_pref là mọi thứ còn lại: lời dặn, sở thích, mục tiêu, phong cách.
2. Chạy triage hai lần, song song:
   - A = T(x): chỉ tin nhắn, không có memory.
   - B = T(x, M_ev): tin nhắn cộng memory loại bằng chứng.
3. Mức cuối s = max(A, B). M_pref không bao giờ vào triage. Nó chỉ dùng để soạn câu trả lời, nên
   phần cá nhân hóa giữ nguyên.

**Bảo đảm theo cấu trúc.** Vì s ≥ A, tỉ lệ bắt ca khẩn khi có memory luôn ≥ khi không có memory.
- Lời dặn và mục tiêu không vào triage, nên không kéo được mức xuống.
- Chẩn đoán cũ có vào B, nhưng A không thấy nó, nên neo cũng không kéo được mức cuối xuống.

**Nói thẳng về tính mới.** Lấy max của nhiều bộ phát hiện là một mẹo bảo thủ quen thuộc. Đóng góp
không nằm ở phép max, mà ở ba điểm:
1. Chỉ ra memory có hai tác động ngược chiều lên an toàn, và tách chúng thành hai góc nhìn.
2. Đo được hiện tượng này trên nhiều model.
3. Chứng minh các cách sửa thông dụng đều hỏng ở một trong hai chiều.

## 2. Đã có ai làm chưa (tra ngày 24/10, khoảng 15 lượt tìm; bổ sung ngày 25/10, 3 lượt)

| Bài | Làm gì | Khác bài mình ở đâu |
|---|---|---|
| **When Personalization Legitimizes Risks** (PS-Bench, arXiv 2601.17887, nộp ARR 2026). Bài gần nhất | Memory lành tính khiến agent "hợp thức hóa" yêu cầu có hại; tỉ lệ tấn công thành công tăng 15,8–243,7 % so với không có memory. Giảm bằng một bước phát hiện rồi tự xem lại (detection–reflection) | Họ đo lỗi *làm điều có hại*; bài mình đo lỗi *bỏ qua cảnh báo* trong y tế. Cách giảm của họ là thêm một bước LLM, không có bảo đảm. Mình dùng cách của họ làm baseline B3 |
| PersistBench (ICML 2026) | Memory làm model chiều theo user (tỉ lệ hỏng trung vị 97 % trên 18 model) và rò rỉ chéo lĩnh vực | Chỉ đo, ở lĩnh vực chung; không có triage lâm sàng, không đề xuất cơ chế |
| Jain và cộng sự, "Interaction Context Often Increases Sycophancy in LLMs" (arXiv 2509.12517, MIT và Penn State; thêm 25/10) | Hồ sơ người dùng trong memory làm model dễ đồng ý hơn, mạnh nhất khoảng 45 % trên Gemini 2.5 Pro; 4/5 model bị ảnh hưởng. Dùng ngữ cảnh thật của 38 người dùng trong 2 tuần | Đo sự gật gù ở dạng chung; không có y tế, không có cảnh báo khẩn, không có cách sửa |
| HealthBench (OpenAI, arXiv 2505.08775; thêm 25/10) | 5.000 hội thoại y tế, tiêu chí chấm do bác sĩ viết. Chủ đề "emergency referrals" có 482 ca, coi cả cảnh báo thiếu lẫn cảnh báo thừa là lỗi | Không có memory hay lời dặn lưu qua phiên. Có thể lấy các ca emergency referrals rồi thêm memory để làm bộ dữ liệu |
| PENGUIN / RAISE (NeurIPS 2025) | Thông tin cá nhân làm điểm an toàn tăng 43,2 % (14.000 kịch bản, 7 lĩnh vực) | Chỉ có chiều có lợi. Bài mình giữ chiều này (nhánh B) và chặn chiều có hại |
| Counterfactual personalization safety (arXiv 2603.16734) | Đo an toàn của agent bằng các cặp ngữ cảnh chỉ khác nhau ở thông tin cá nhân (trên AgentHarm) | Chỉ đo, không có cơ chế; không phải y tế |
| BiasMedQA (npj Digital Medicine 2024); Chen et al., "When helpfulness backfires" (npj Digital Medicine 2025); MedPRESS (arXiv 2608.02520) | Bệnh nhân tự chẩn đoán, yêu cầu phi lý, hoặc gây áp lực trong hội thoại làm LLM y tế trả lời sai | Thiên lệch nằm trong câu hỏi hoặc hội thoại hiện tại, không phải memory lưu qua các phiên; không có cơ chế có bảo đảm |
| LLM neo vào triệu chứng đầu trong triage cấp cứu (arXiv 2609.22904) | Mức triage bám vào triệu chứng chính kể đầu tiên; sửa bằng prompt không ăn thua | Ủng hộ giả thuyết về neo; không có memory, không có cách sửa |
| Instruction hierarchy: IHEval (≤ 48 %), ManyIH-Bench (khoảng 40 %), "Control illusion" (AAAI 2026) | Model không giữ được thứ bậc system > user khi hai bên xung đột | Đây là lý do baseline "prompt ưu tiên an toàn" (B2) khó đáng tin. Các cách sửa của họ cần huấn luyện, không áp dụng được cho model gọi qua API |
| Dual-LLM pattern; CaMeL (DeepMind 2025) | Tách quyết định khỏi dữ liệu không tin cậy để chống prompt injection | Cùng tinh thần cô lập. Khác ở chỗ memory vừa có lợi vừa có hại, nên không cô lập hết mà dùng hai góc nhìn rồi lấy max |
| Survey "Personalization Meets Safety" (arXiv 2606.09038) | Hệ thống hóa rủi ro và các cách giảm | Phần đọc được không có cách nào: chạy triage không memory, giới hạn loại memory, hay bảo đảm một chiều. Cũng không có triage y tế. Chưa đọc hết mục 10 |

**Kết luận.** Trong phạm vi đã tra, chưa thấy bài nào làm hai thứ:
- hiện tượng "memory làm trợ lý sức khỏe bỏ qua cảnh báo";
- cơ chế có bảo đảm "chỉ nâng, không hạ".

Khi viết bài phải ghi "to our knowledge". PS-Bench là bài phải so sánh trực tiếp.

Cập nhật 25/10: việc đo "memory làm model chiều theo người dùng" ở dạng chung đã có người làm
(PersistBench, Jain và cộng sự). Paper phải dựa vào ba khoảng trống ghi ở
[[paper-memory-safety-results]] mục 7.

## 3. Phải chứng minh gì, hơn ai, bằng gì

### Ba claim

- **C1. Hiện tượng có thật:** memory có hại làm giảm recall ca khẩn so với khi không có memory.
  Phải thấy được trên nhiều model và nhiều loại memory.
- **C2. Các cách sửa thông dụng đều hỏng ở một chiều:**
  - Bỏ memory khỏi triage thì mất các ca cần tiền sử mới nhận ra.
  - Prompt ưu tiên an toàn, detection–reflection, lọc memory bằng LLM, hay chỉ lọc theo loại thì
    vẫn bị kéo xuống ở ít nhất một loại memory.
- **C3. EOM giữ được cả hai chiều:**
  - Recall khi memory có hại ≥ khi không có memory (bảo đảm theo cấu trúc).
  - Recall trên các ca cần tiền sử ngang với cách đưa memory vào mọi chỗ.
  - Báo động sai không tăng đáng kể, cá nhân hóa giữ nguyên.
  - Chi phí chỉ thêm 1 lời gọi ngắn chạy song song.

### Baseline (chạy lại tất cả trong cùng điều kiện)

| | Mô tả | Lấy từ đâu |
|---|---|---|
| B0 | Memory vào mọi chỗ (hiện trạng) | ECA, PersistBench, PS-Bench |
| B1 | Không dùng memory trong triage | Thiết kế stateless |
| B2 | Prompt: an toàn ưu tiên hơn lời dặn và sở thích | Instruction hierarchy dạng prompt |
| B3 | Phát hiện rồi tự xem lại | Cách giảm của PS-Bench |
| B4 | LLM lọc memory xung đột với an toàn trước khi triage | Lọc memory khi truy hồi |
| B5 | Chỉ lọc theo loại (chỉ dùng M_ev, không lấy max) | Ablation của EOM |
| EOM | max(A, B) cộng lọc theo loại | Đề xuất |

### Metric

- **Chính:** recall ca khẩn (L2 và L3) khi memory có hại.
- Recall trên ca cần memory (chi tiết quyết định chỉ nằm trong memory).
- Báo động sai trên tin nhắn lành tính và câu bẫy.
- Cá nhân hóa giữ lại: câu trả lời cho tin lành tính còn dùng memory liên quan hay không.
- Chi phí: số lời gọi, số token, độ trễ p50/p95.

### Bằng chứng cần có

- Kiểm định cặp McNemar cho từng model, khoảng tin cậy Wilson, mỗi cấu hình chạy 3 lần.
- Ít nhất 4 họ model: DeepSeek v4-flash (model của ECA), cùng các model mở trên Groq:
  `llama-3.1-8b-instant`, `llama-3.3-70b-versatile`, `openai/gpt-oss-20b`. Thêm một model đóng
  nếu có ngân sách.
- Tiếng Việt và tiếng Anh, có dấu và không dấu.
- Hai nguồn memory:
  - tiêm tay theo kịch bản;
  - memory do chính memory v2 trích từ các hội thoại trước, để sát thực tế.
- Nhãn vàng theo tiêu chí NHS, có 2 người duyệt và báo cáo κ. Lý tưởng là có thêm 1 người chuyên
  môn (bác sĩ hoặc chuyên viên vật lý trị liệu) duyệt một mẫu.

### Dữ liệu

- Tin nhắn: dùng pipeline H2 (persona từ Nemotron-Personas-Vietnam, nhãn theo tiêu chí NHS).
- Các điều kiện memory:
  - không có memory;
  - lời dặn;
  - chẩn đoán cũ gây neo, ví dụ thoát vị đĩa đệm đi kèm triệu chứng chùm đuôi ngựa (thoát vị đĩa
    đệm lại chính là nguyên nhân hay gặp nhất của hội chứng này);
  - mục tiêu;
  - bằng chứng (tai nạn, bệnh nền).
- Ca cần memory: chuyển chi tiết quyết định (ví dụ chuyện tai nạn) từ tin nhắn sang memory.
- Quy mô: khoảng 200 tin nhắn × 6 điều kiện × 4–5 model, tức khoảng 6.000 lời gọi triage.
  DeepSeek tốn vài USD; các model mở chạy free tier.

## 4. Khi nào bỏ

- Nếu S3 với DeepSeek không thấy memory kéo recall xuống rõ (không loại memory nào làm nó xuống
  ≤ 18/26), thì chạy thêm 1–2 model mở. Nếu vẫn không model nào bị kéo xuống thì bỏ.
- Nếu B2 hoặc B3 đã giữ được recall mà không mất các ca cần memory, thì EOM không hơn ai: bỏ,
  hoặc đổi claim.

## 5. Lịch và chi phí

- S3 (smoke test): nửa ngày, khoảng 0,05 USD.
- Bản đầy đủ: 6–8 tuần.
  - Dữ liệu và duyệt nhãn: 2 tuần.
  - Chạy nhiều model: 1 tuần.
  - Baseline và ablation: 2 tuần.
  - Viết bài: 2 tuần.
- Nơi nộp: ACL qua ARR (khoảng đầu 01/2027, theo notes của Tri), hoặc một journal tin học y tế.
  Với ICIIT 30/10 thì chỉ kịp bản sơ bộ.

## Nguồn

- PS-Bench: https://arxiv.org/abs/2601.17887
- PersistBench: https://arxiv.org/pdf/2602.01146
- PENGUIN / RAISE: https://arxiv.org/html/2505.18882
- Counterfactual personalization safety: https://arxiv.org/abs/2603.16734
- Survey Personalization Meets Safety: https://arxiv.org/html/2606.09038v1
- BiasMedQA: https://arxiv.org/abs/2402.08113v3
- When helpfulness backfires (thông cáo MGB): https://www.massgeneralbrigham.org/en/about/newsroom/press-releases/large-language-models-prioritize-helpfulness-over-accuracy-in-medical-contexts
- MedPRESS: https://arxiv.org/pdf/2608.02520
- Neo trong triage cấp cứu: https://arxiv.org/abs/2609.22904v2
- Instruction hierarchy: https://arxiv.org/abs/2502.08745v2 ·
  https://underline.io/lecture/142840-control-illusion-the-failure-of-instruction-hierarchies-in-large-language-models
- CaMeL: https://www.infoq.com/news/2025/04/deepmind-camel-promt-injection
