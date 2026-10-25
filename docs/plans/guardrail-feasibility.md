---
tags: [plan, guardrail, safety, feasibility]
date: 2026-10-24
author: K, theo yêu cầu của Tri
status: nháp v2, chưa duyệt
---

# Guardrail layer: kế hoạch đánh giá độ khả thi

Phần nghiên cứu (paper) tách riêng ở [[paper-memory-safety]].

## Tóm tắt

- Ý của Tri: guardrail là **một layer riêng**, kiểm input của user và output của LLM. Ngoài các
  kiểm tra phổ thông của provider, layer này kiểm thêm các vấn đề an toàn sức khỏe. Hiện mới ở mức
  dự tính.
- Đề xuất v2:
  - Guardrail là một package riêng, gọi ở cửa API và bọc ngoài LangGraph.
  - Chỉ riêng phần triage sức khỏe có dùng memory được graph gọi tại một điểm.
  - Bản v1 (đặt guardrail làm một node trong graph) bị bỏ, vì không đúng ý "layer riêng".
- Có 5 spike, khoảng 4 ngày công, dưới 1 USD tiền API. Quyết định làm hay không theo mục 6.

## 1. Hiện trạng: safety của ECA đang nằm ở đâu

| Thành phần | Làm gì | Ở đâu | Số đo |
|---|---|---|---|
| Planner | Gắn tag an toàn (`red_flag_screen`, `referral_advice`, `scope_disclaimer`…) | `nodes/planner.py` | Pilot H2: bắt 22/26 ca khẩn; bỏ sót cả 4 ca đau sau chấn thương; khuyên khám GP 5/12; bản bỏ dấu báo động sai 2/9 |
| Synthesizer | Phát câu red flag ở đầu; đặt mặt avatar trung tính | `synthesizer.py:681`, `synthesizer.py:792` | — |
| Grader | Chèn câu cuối (khuyên khám, nguồn, disclaimer); judge kiểm, thiếu thì viết thêm | `grader.py:397`, `grader.py:441` | 781 ms mỗi lời gọi; chấm sai 5/85 nhãn; 0 ca thiếu ở V8 |
| Memory v2 | `condition` và `restriction` lên tier 1 ngay lần đầu (Tri đang bỏ trên máy) | `fact_extractor.py:91`, `memory.py:98` | — |
| Provider | Code không có lời gọi moderation hay `safety_settings` nào | grep ngày 24/10 | Chưa đo |

Lỗi đo được nằm ở đầu vào: planner bỏ sót red flag. Đầu ra gần như không có lỗi: 0 ca thiếu phần
ở V8, 0 ca sai liều lượng qua 13 lần probe.

## 2. Đặt guardrail ở đâu

### Doanh nghiệp đang làm thế nào

Cả hai kiểu đều phổ biến, nhưng mỗi kiểu dùng cho một loại kiểm tra khác nhau.

| Kiểu | Ví dụ | Dùng cho |
|---|---|---|
| Layer riêng (gateway hoặc dịch vụ guard) | AWS Bedrock `ApplyGuardrail`: kiểm text mà không cần gọi model, dùng được với model bên thứ ba, gọi ở bất kỳ điểm nào trong luồng, có nhãn INPUT/OUTPUT. AI gateway: Portkey có guardrail input/output (chặn, ghi log, thử lại); Kong tích hợp AWS Guardrails, Azure Content Safety, Google Model Armor, Lakera. NeMo Guardrails chạy dạng microservice hoặc `nemoguardrails server` | Chính sách chung cho nhiều app và nhiều model: nội dung độc hại, PII, jailbreak |
| Trong app/agent | OpenAI Agents SDK: input guardrail chạy song song với agent (mặc định) hoặc chặn trước; output guardrail chạy sau khi agent xong; tripwire dừng run. LangChain v1 middleware: trước/sau agent, quanh lời gọi model và tool, có sẵn PII middleware. NeMo Guardrails dạng thư viện tích hợp với LangChain/LangGraph | Kiểm tra cần ngữ cảnh của app: lịch sử hội thoại, memory, tool |

Kết luận: layer riêng hợp với kiểm tra phổ thông, còn kiểm tra cần ngữ cảnh thì thường nằm trong
app. Các bài so sánh gateway cũng nói như vậy: gateway là một điểm thực thi tập trung, nhưng team
nào cần chính sách riêng của app thì vẫn giữ một phần kiểm tra bên trong app.

### Ba chỗ đặt có thể chọn cho ECA

| | (1) Node trong LangGraph (v1, bỏ) | (2) Package riêng gọi ở cửa API | (3) Dịch vụ riêng |
|---|---|---|---|
| Đúng ý "layer riêng" | Không, dính vào graph | Có, bọc ngoài graph | Có, tách hẳn |
| Đọc memory | Dễ, vì có sẵn state | Được, qua hàm đọc memory | Phải gọi DB hoặc API riêng |
| Kiểm output đang stream | Khó, logic rải trong synthesizer và grader | Tự nhiên: lọc luồng SSE trước khi gửi xuống client | Phải proxy cả luồng SSE |
| Độ trễ | Song song với planner | Input chạy song song với graph | Thêm một bước mạng |
| Tự host guard model | Không được (RAM `vva-agent` đỉnh 1.917/2.048 MB) | Không được | Được |
| Hạ tầng thêm | 0 | 0 | Thêm CDK, cold start |

### Đề xuất

```
client ──► api/main.py ──┬──► guardrail.check_input(x)   (G: kiểm phổ thông, chạy song song)
                         │        chặn → hủy graph, trả câu cố định
                         └──► LangGraph: memory → planner → … → synthesizer → grader
                                                       ▲
                                guardrail.triage(x, M_ev) = max(A, B)   (H: một điểm gọi)
client ◄── guardrail.check_output (theo câu, trên luồng SSE) ◄── stream
```

1. **Guardrail là một package riêng** (`guardrail/`), do Tri làm chủ, có test và config chính sách
   riêng.
2. **Input:** gọi ở `api/main.py`, chạy song song với graph ngay khi nhận request. API giữ luồng
   SSE lại cho tới khi có kết quả input. Kiểm tra G nhanh hơn planner nên thường không phải chờ.
   Nếu bị chặn thì hủy graph và trả câu cố định.
3. **Output:** lọc luồng SSE theo từng câu, trước khi gửi xuống client và TTS. Chỉ làm nếu S4 đạt.
   Grader hiện tại vẫn lo phần viết thêm ở cuối.
4. **Triage sức khỏe (H):** code nằm trong package guardrail, nhưng graph gọi nó tại một điểm
   trước synthesizer. Lý do: H cần memory, và kết quả của H quyết định câu cảnh báo do code chèn.
   Graph chỉ gọi hàm, không chứa logic guardrail. Tag cuối là hợp của tag planner và tag H.
5. **Khi cần tự host guard model**, hoặc khi nhiều app dùng chung chính sách, chuyển package thành
   dịch vụ riêng (phương án 3). Interface giữ nguyên.

## 3. Câu hỏi khả thi cần trả lời

1. Guard phổ thông nào chạy được với tiếng Việt, kể cả không dấu? Độ trễ và chi phí bao nhiêu? Có
   báo nhầm trên tin nhắn sức khỏe không?
2. Triage sức khỏe tách riêng có bắt red flag tốt hơn tag của planner không, trong ngân sách độ
   trễ?
3. Lọc output trên luồng SSE có làm chậm câu đầu tiên không? Và có gì để bắt không?
4. Guardrail đọc memory thế nào? Câu này là phần paper ([[paper-memory-safety]]).
5. Guardrail thay hay bọc phần tag an toàn của planner? Quyết định này của Tri.

## 4. Spike

| Spike | Làm gì | Thời gian | Chi phí | Đạt khi |
|---|---|---|---|---|
| S1 | Thử Qwen3Guard-Gen (0.6B/4B/8B, tuyên bố 119 ngôn ngữ, chưa có số riêng cho tiếng Việt) và Llama Guard 4 12B trên Groq. Dữ liệu: khoảng 40 tin có hại và 60 tin sức khỏe lành tính, toàn bộ tổng hợp, có thêm bản bỏ dấu | ~1 ngày | ~0 | Báo nhầm trên tin sức khỏe ≤ 2 %; recall nhóm tự hại ≥ 90 %; p95 ≤ 300 ms |
| S2 | Prompt triage riêng (L0–L3, có red flag sau chấn thương) trên 58 câu pilot (dev); so với planner và với hợp của hai | ~1 ngày | < 0,1 USD | Hợp của hai bắt ≥ 25/26 ca khẩn, không thêm báo động sai |
| S3 | Smoke test cho paper: 26 ca khẩn × 3 loại memory có hại, chạy trên planner và trên max(A, B) | ~0,5 ngày | ~0,05 USD | Xem [[paper-memory-safety]] mục 4 |
| S4 | Lọc output theo câu trên luồng SSE: đo câu đầu tiên chậm thêm bao nhiêu; đếm số ca bắt được trên 51 câu probe | ~1 ngày | ~0 | Chậm thêm ≤ 300 ms, và có ca đáng chặn |
| S5 | Quyền riêng tư: provider nào nhận tin nhắn của user thì phải ghi vào trang Privacy; phương án tự host Qwen3Guard-0.6B ở dịch vụ riêng, đo độ trễ trên CPU | ~0,5 ngày | 0 | Chốt được phương án |

## 5. Phân công đề xuất

- Tri: chủ package guardrail. Quyết câu hỏi 5 và phương án ở S5.
- Nguyen và K: S2, S3 (dùng lại pipeline `local_tests/safety_eval`). Làm cả S1 nếu Tri muốn.
- S4: người nắm phần SSE và TTS.

## 6. Tiêu chí làm hay không

- Làm input check nếu S1 đạt. Nếu không có guard nào đạt với tiếng Việt, input chỉ gồm triage sức
  khỏe; phần kiểm phổ thông để sau.
- Làm triage sức khỏe riêng nếu S2 đạt.
- Chỉ lọc output trên SSE nếu S4 đạt. Nếu không, giữ grader hiện tại.

## 7. Rủi ro

- Hai nguồn sự thật (tag của planner và của guardrail). Cách giảm: tag cuối là hợp của hai bên, và
  chỉ có một đường chèn câu an toàn.
- Guard phổ thông báo nhầm trên nội dung sức khỏe (đau, máu, thuốc).
- Báo động quá nhiều làm người dùng quen tay bỏ qua cảnh báo. Đo bằng các câu bẫy.
- Thêm provider là thêm một bên xử lý dữ liệu của user.
- Tự host guard model thì phải tách thành dịch vụ riêng, nghĩa là thêm hạ tầng.

## Nguồn

- AWS Bedrock ApplyGuardrail: https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-use-independent-api.html
- OpenAI Agents SDK guardrails: https://openai.github.io/openai-agents-python/guardrails/
- LangChain guardrails (middleware): https://docs.langchain.com/oss/python/langchain/guardrails
- NeMo Guardrails (thư viện và microservice): https://docs.nvidia.com/nemo/guardrails/latest/introduction.html
- So sánh AI gateway: https://www.getmaxim.ai/articles/top-5-ai-gateways-for-implementing-guardrails-in-ai-applications/
- Qwen3Guard: https://arxiv.org/pdf/2510.14276v1
- Llama Guard 4 trên OpenRouter: https://openrouter.ai/meta-llama/llama-guard-4-12b
