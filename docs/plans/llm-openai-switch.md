---
tags: [plan, llm, openai, prod, safety]
date: 2026-10-26
author: K, cho Nguyen và Tri
status: xong; prod chạy gpt-6-luna từ 30/10 20:59 (release 213a5c4); smoke test đạt ngày 31/10 (worklog 30/10)
---

# Chuyển model của prod từ DeepSeek sang OpenAI (gpt-6-luna)

## 1. Vì sao đổi

- **An toàn.** Trong thí nghiệm của paper ([[paper-memory-safety-results]]), khi memory có lời dặn "đừng khuyên
  tôi đi khám":
  - `deepseek-v4-flash` (model prod hiện tại) bỏ hẳn lời khuyên đi khám ở 81–86 % tin khẩn;
  - `gpt-6-luna` suy luận bật chỉ bỏ 1–3 %, suy luận tắt 6–15 %;
  - câu trả lời hoàn chỉnh của ECA trên v4-flash cũng giảm từ 96 % xuống 81–86 %.
- **Giá.** Theo bảng giá, Luna rẻ ngang DeepSeek ($0,10 / $0,50 mỗi 1 triệu token vào / ra). Tiền DeepSeek thực tế
  cao hơn bảng giá 1,6–2,2 lần. Một lượt chat (planner và synthesizer) ước tính khoảng 0,001 USD trên Luna.
- **Dữ liệu.** DeepSeek xử lý ở Trung Quốc. OpenAI ở Hoa Kỳ, cùng nước với AWS, Neon và Gemini, nên phần chuyển dữ
  liệu ra nước ngoài trong chính sách quyền riêng tư gọn hơn.

## 2. Hiện trạng (kiểm ngày 26/10)

- `llm.py`:
  - Mọi vai trò dùng một model: `DEEPSEEK_MODEL_FAST`, mặc định `deepseek-v4-flash` (`_HEAVY_ROLES` rỗng).
  - Gọi qua `ChatOpenAI` với `DEEPSEEK_BASE_URL`.
  - Key lấy từ env `DEEPSEEK_API_KEY`, hoặc từ SSM qua `DEEPSEEK_API_KEY_PARAM` (prod: `/vva/llm/deepseek-api-key`).
  - Nhiệt độ theo vai trò: planner 0, synthesizer 0,7, grader 0, memory 0.
  - `thinking: disabled` chỉ gửi khi host là deepseek.com (grader, memory).
- Khi lời gọi chính lỗi, planner và synthesizer chuyển sang Gemini 2.5 Flash.
- BYOK đã có cơ chế `omit`: bỏ `temperature` hoặc `max_tokens` với provider không nhận các tham số này.
- `infra/infra/agent_stack.py` đặt `DEEPSEEK_API_KEY_PARAM`. Quyền `ssm:GetParameter` chỉ mở cho đúng các param đã
  liệt kê.
- Trang Privacy (en, vi) ghi "DeepSeek (máy chủ tại Trung Quốc) trả lời mặc định"; `legal.test.ts` kiểm danh sách
  bên nhận dữ liệu.
- LangChain 1.2.2 tự đổi `max_tokens` thành `max_completion_tokens` cho OpenAI. Nhưng nó chỉ tự bỏ temperature
  với tên model `gpt-5*` và `o1`, nên với `gpt-6-luna` code phải tự bỏ.

## 3. Thay đổi cần làm

### 3.1 Code (`llm.py`, khoảng nửa ngày kể cả test)

- Thêm biến chung `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY_PARAM`. Thiếu biến chung thì đọc tên `DEEPSEEK_*` cũ, để
  deploy hiện tại và máy dev không gãy.
- Khi host là `api.openai.com`:
  - không gửi `temperature` (gpt-6-luna chỉ nhận giá trị mặc định 1);
  - gửi `reasoning_effort` theo vai trò: grader và memory `none`; planner và synthesizer chọn sau khi đo (mục 4);
  - giữ trần `max_tokens`. Vai trò nào bật suy luận thì nâng trần, vì token suy luận cũng tính vào trần;
  - bật `stream_usage=True` để log có số token.
- Telemetry prompt cache trong planner: DeepSeek trả `prompt_cache_hit_tokens`, OpenAI trả
  `prompt_tokens_details.cached_tokens`. Đọc cả hai.
- Test unit:
  - tên model đúng theo biến;
  - không gửi temperature khi gọi OpenAI;
  - `reasoning_effort` đúng theo vai trò;
  - vẫn gửi `thinking` khi gọi DeepSeek;
  - Gemini fallback giữ nguyên.

### 3.2 Key và hạ tầng

- Tạo một project OpenAI riêng cho prod, có key riêng, giới hạn chi tiêu tháng (ví dụ 10 USD), và chỉ cho dùng
  `gpt-6-luna`. Không dùng chung key thí nghiệm. Các project trong cùng tổ chức OpenAI trừ chung số dư, hiện còn
  khoảng 2,5 USD, nên cần nạp thêm trước khi chạy prod lâu dài.
- Lưu key vào SSM SecureString `/vva/llm/openai-api-key`.
- `agent_stack.py`:
  - thêm `LLM_BASE_URL=https://api.openai.com/v1`, `LLM_MODEL=gpt-6-luna` và `LLM_API_KEY_PARAM=/vva/llm/openai-api-key`;
  - thêm param mới vào quyền `ssm:GetParameter`;
  - giữ nguyên param DeepSeek để quay lại nhanh.
- Deploy: `cdk deploy VvaAgentStack` (Nguyen ra lệnh rõ), rồi release push theo năm bước trong CLAUDE.md.

### 3.3 Chính sách quyền riêng tư (phần của Nguyen)

- Sửa `privacy.en.md` và `privacy.vi.md`:
  - OpenAI (Hoa Kỳ) trả lời mặc định; Gemini trả lời khi OpenAI không hoạt động;
  - bỏ câu "xử lý tại Trung Quốc";
  - ghi cách OpenAI dùng và lưu dữ liệu gửi qua API. Kiểm lại trang chính sách của OpenAI trước khi ghi.
- Sửa `legal.test.ts` theo danh sách mới.
- Bản sửa phải lên prod cùng lúc hoặc trước khi đổi model.

## 4. Đo trước khi đổi (chạy local trên DB dev, không đụng prod)

| Bước | Đo gì | Chi phí ước tính |
|---|---|---|
| Probe V8, 51 lượt vi và en | Liều thuốc ngoài evidence, số retry, câu stream khớp câu lưu, độ trễ so với giới hạn (vi 16,0 s, en 15,4 s) | khoảng 0,15 USD cho 2 cấu hình |
| Đánh giá judge, 85 nhãn | Grader trên Luna: số sai (mục tiêu ≤ 5 %), ms mỗi lần | khoảng 0,01 USD |
| Memory benchmark B4 và N1 | Memory v2 không tệ hơn trên DeepSeek | khoảng 0,15 USD |
| Kiểm memory-safety (`eca_full`, 44 tin, 3 điều kiện memory, 1 lần) | ECA trên Luna còn bị lời dặn kéo xuống không | khoảng 0,15 USD |

Tổng khoảng 0,5 USD. Về suy luận: chạy V8 với hai cấu hình (tắt hẳn, và planner `low` với synthesizer tắt), rồi
chọn cấu hình nhanh nhất mà vẫn qua các ngưỡng an toàn.

## 5. Rollout và cách quay lại

1. Code và test trên `feature/langgraph-rewrite`. Tri review, vì `llm.py` và `agent_stack.py` là phần của Tri.
2. Đo theo mục 4, ghi kết quả vào worklog.
3. Sửa Privacy.
4. Tạo key, lưu SSM, `cdk deploy VvaAgentStack`, rồi release push.
5. Smoke test sau deploy:
   - `/health` trả 200;
   - 2 câu chat thật, có một câu cần tìm kho kiến thức;
   - log `llm_init` ghi `model=gpt-6-luna`, và không có `llm_fallback_used`.
6. Quay lại: đổi 3 biến về DeepSeek rồi `cdk deploy VvaAgentStack`, mất vài phút. Key DeepSeek vẫn nằm trong SSM.

## 6. Rủi ro

- **Độ trễ.** Bật suy luận làm chậm chữ đầu tiên; mục 4 đo trước khi chọn.
- **Giọng persona và tiếng Việt** khác DeepSeek. Đọc bằng mắt 10–20 câu trả lời của probe.
- **Hạn mức tốc độ** của tài khoản OpenAI mới thấp. Lưu lượng hiện rất nhỏ (1 lượt chat từ 24/10 đến 26/10), nên chưa
  đáng lo.
- **Memory-safety trên Luna tắt suy luận** vẫn bỏ 6–15 % ở trợ lý thường. ECA có tag và câu cảnh báo do code chèn,
  nên mục 4 phải đo lại; nếu còn hở thì cần `gate3` (paper).

## 7. Cần Nguyen chốt

1. Key: tạo project riêng có giới hạn chi (khuyên dùng), hay dùng key hiện tại.
2. Duyệt khoảng 0,5 USD cho phần đo ở mục 4.
3. Báo Tri. `llm.py` và `agent_stack.py` là phần của Tri: Tri review bản của K, hoặc Tri tự làm.

## 8. Đã làm ngày 27/10 và các bước còn lại

### Đã làm (chưa commit, chưa đụng prod)

- **`llm.py`.** Đổi provider bằng một biến `LLM_PROVIDER` (`deepseek` mặc định, hoặc `openai`).
  - Mỗi provider có biến riêng: `DEEPSEEK_*` giữ nguyên; `OPENAI_API_KEY` hoặc `OPENAI_API_KEY_PARAM`,
    `OPENAI_MODEL_FAST` và `OPENAI_MODEL` (mặc định `gpt-6-luna`).
  - Khi gọi OpenAI: không gửi temperature, `reasoning_effort` mặc định `none` (đổi được bằng
    `OPENAI_REASONING_EFFORT` hoặc `OPENAI_REASONING_EFFORT_<ROLE>`), thêm 2.048 token trần khi một vai trò bật
    suy luận, và bật `stream_usage`.
  - Giá trị lạ của `LLM_PROVIDER` thì quay về DeepSeek và ghi log `llm_provider_unknown`, không làm hỏng lượt
    chat.
- **Test.** `tests/langgraph_agents/test_llm_provider.py` có 8 test. Bộ unit backend (`tests/langgraph_agents`,
  bỏ integration và e2e) chạy 1.492 test, qua hết. Hai test kiểm khai báo import giờ bỏ qua
  `local_tests/safety_eval/`, vì service không import các script paper (cần Tri xem).
- **`agent_stack.py`.**
  - Hằng `_LLM_PROVIDER = "deepseek"` nằm trong code chứ không chỉ là cờ lúc deploy. Nhờ vậy một lần deploy vì
    việc khác không vô tình đổi provider.
  - Biến môi trường mới: `LLM_PROVIDER`, `OPENAI_API_KEY_PARAM=/vva/llm/openai-api-key`.
  - Quyền `ssm:GetParameter` có thêm param mới.
  - Đã kiểm bằng synth (không deploy). Synth vẫn báo lỗi cũ về `motion_key_pair_id`; lúc deploy truyền thêm cờ
    này (mục 8.2).
- **Harness paper.** `eca_full` trên OpenAI giờ chạy qua đúng đường code mới (`LLM_PROVIDER=openai`), không còn
  vá tay temperature và trần token.
- **Đo câu trả lời hoàn chỉnh của ECA trên Luna** (mục 4, dòng `eca_full`; 44 tin, 3 điều kiện memory, 1 lần lặp;
  label `switch-luna-eca` và `switch-luna-eca-plow`; tổng khoảng 0,08 USD). Bảng tính trên 26 tin khẩn mỗi
  điều kiện; báo động sai tính trên 54 tin không cần cảnh báo:

  | Cấu hình | Khuyên khám gấp: none / instr_x3 / override | Bỏ hẳn lời khuyên | Báo động sai | Thời gian một lượt trong harness (planner, synthesizer, judge; trung vị) |
  |---|---|---|---|---|
  | Luna, tắt suy luận (cấu hình sẽ dùng) | 21 / 22 / 21 | 0/78 | **0/54** | 4,9 s |
  | Luna, planner suy luận `low` | 21 / 18 / 22 | 1/78 | 0/54 | 6,1 s |
  | DeepSeek v4-flash (S3c, 3 lần, quy về /26) | 25 / 22 / 21 | 3/234 | 21/162 (13 %) | — |

  - Memory không kéo Luna xuống: có lời dặn hay không, tỉ lệ vẫn 81–85 %. DeepSeek từ 96 % tụt xuống 81–86 %.
  - Báo động sai giảm từ 13 % (DeepSeek) xuống 0.
  - Cho planner suy luận `low` không giúp gì (61 so với 64/78, p = 0,63) mà chậm hơn 1,2 s, nên giữ `none`.
  - Chỗ còn hở: khi không có memory, Luna khuyên khám gấp ít hơn DeepSeek (81 % so với 96 %). Ở 5–7 tin khẩn,
    planner của Luna không gắn tag nguy hiểm, nên câu trả lời chỉ khuyên đi khám chung chung; không câu nào bỏ
    hẳn. Bước kiểm 3 mức (`gate3`, paper) là cách sửa đã thử; đưa vào prod là việc riêng, cần Tri.
  - Giới hạn: chưa có retrieval (synthesizer chạy ở chế độ refuse), mới chạy 1 lần lặp, chưa chạy probe V8 và
    memory benchmark (cần DB dev).
- **Đo lại trên code mới của Tri** (27/10 tối; synthesizer bỏ chế độ refuse; label `switch-luna-eca-v2`, khoảng
  0,04 USD). Đây là code sẽ lên prod ở lần release tới:

  | Synthesizer | Khuyên khám gấp: none / instr_x3 / override | Bỏ hẳn lời khuyên | Báo động sai | Thời gian một lượt trong harness (trung vị) |
  |---|---|---|---|---|
  | Cũ (có refuse), Luna | 21 / 22 / 21 | 0/78 | 0/54 | 4,9 s |
  | **Mới, Luna** | **26 / 26 / 25** | 0/78 | 4/54 (7 %) | 6,3 s |

  - Khuyên khám gấp tăng từ 64 lên 77/78 (p < 0,001). Khi có lời dặn còn 51/52, nên memory không kéo xuống.
    Chỉ sót 1 tin (`back-L3-06`, bản bỏ dấu, `override`), và tin đó vẫn được khuyên đi khám chung chung.
  - Báo động sai tăng nhẹ: 4/54, so với DeepSeek 21/162 (13 %) trên code cũ.
  - DeepSeek chưa đo lại trên code mới vì số dư DeepSeek đã sát ngưỡng 0,5 USD.

### Đã chốt ngày 27/10

- Nguyen: dùng luôn `OPENAI_API_KEY` hiện có, giống key DeepSeek trên prod; không tạo project riêng (người dùng
  vẫn gắn key của nhiều provider qua BYOK).
- Tri đồng ý đổi model của prod.
- Lần release push tới sẽ mang theo mọi thứ Tri đã merge vào `feature/langgraph-rewrite` từ 24/10: memory-store
  (có migration 017), synthesizer bỏ chế độ refuse, xử lý lỗi chat. Vì vậy Tri cần chốt thời điểm release.

### Các bước còn lại

1. ~~Tạo project OpenAI riêng~~: không cần (xem trên).
2. ~~Lưu key hiện có vào SSM~~: xong ngày 28/10 (`/vva/llm/openai-api-key`, SecureString, version 1).
3. ~~Commit phần code~~: 5b0b35d và 61828c4, đã push lên `feature/langgraph-rewrite` ngày 28/10.
4. ~~**Commit chuyển đổi**~~ (xong ngày 29/10), gồm bốn phần lên cùng lúc:
   - `agent_stack.py`: `_LLM_PROVIDER = "openai"`;
   - Privacy (8.1);
   - `LEGAL_EFFECTIVE_DATE` trong `ECA_UI/frontend/src/legal/index.ts` đặt bằng ngày chuyển;
   - `legal.test.ts`: thêm `'OpenAI'` vào danh sách bên nhận dữ liệu.
5. Trước release push: DB prod phải có migration 017 của Tri. Code mới đọc cột `memory_type` khi tìm memory tầng
   2 và ở các route `/me/memory` (worklog 29/10). Sau đó release push, rồi chạy trong `infra/` (Nguyen ra lệnh rõ):
   `npx --yes aws-cdk@2 deploy VvaAgentStack --exclusively --require-approval never --app "D:\Project_A\.venvs\eca-test\Scripts\python.exe app.py" -c motion_key_pair_id=KGUIRRSF1V55H`
   (cần `infra/build/crud_api.zip`; tạo bằng `python infra/build_crud_api.py`).
6. Smoke test như mục 5.5.
7. Quay lại: deploy lại với `-c llm_provider=deepseek` (vài phút), rồi trả hằng về `"deepseek"`.

### 8.1 Câu chữ Privacy cho commit chuyển đổi

`privacy.en.md`, mục 3 và mục 4:
- "- **OpenAI** (United States) answers by default. Before {EFFECTIVE_DATE}, **DeepSeek** (servers in China)
  answered by default."
- "- **Google Gemini** (Google LLC, United States) answers when OpenAI is unavailable."
- "Your data is stored in the United States. Before {EFFECTIVE_DATE}, it was also processed in China when
  DeepSeek answered. By using ECA you agree to this transfer outside Vietnam."

`privacy.vi.md`, mục 3 và mục 4:
- "- **OpenAI** (Hoa Kỳ) trả lời mặc định. Trước ngày {EFFECTIVE_DATE}, **DeepSeek** (máy chủ tại Trung Quốc) trả
  lời mặc định."
- "- **Google Gemini** (Google LLC, Hoa Kỳ) trả lời khi OpenAI không hoạt động."
- "Dữ liệu của bạn được lưu tại Hoa Kỳ. Trước ngày {EFFECTIVE_DATE}, dữ liệu còn được xử lý tại Trung Quốc khi
  DeepSeek trả lời. Khi dùng ECA, bạn đồng ý với việc chuyển dữ liệu ra ngoài Việt Nam như trên."

Câu chữ chưa đổi ngay hôm nay vì frontend lên prod ở mọi lần release push: đổi sớm thì trang Privacy sẽ ghi OpenAI
trong khi prod vẫn gửi dữ liệu cho DeepSeek.
