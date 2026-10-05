---
date: 2026-10-03
tags: [tracking, review, risk, deploy]
---

# Rà soát sau merge 03/10/2026: rủi ro và việc cần làm

> **Phạm vi:** 51 commit của nhóm (26/09–03/10) được merge vào `feature/langgraph-rewrite`
> (commit `4c35254`), cộng 11 commit lên `origin` sau đó.
> **Thời điểm kiểm prod:** 03/10 21:04, chỉ đọc (lệnh AWS `get`/`list`), không sửa gì trên AWS hay Neon.
> **Người kiểm:** Nguyen (cùng K).
> Liên quan: [[tech-debt]] (mục "Agent context — nợ ghi 03/10" của Tri), worklog [[03-10-2026]].

## Tóm tắt

| # | Rủi ro | Mức | Trên prod lúc 21:04 | Ai quyết / ai làm | Việc |
|---|---|---|---|---|---|
| 1 | Một tool lỗi làm hỏng cả lượt chat | 🟠 | Có thể đã xảy ra (chưa đo) | Tri (`graph.py`) | Bắt lỗi tool thành kết quả JSON, pin `langgraph` |
| 2 | Route feedback chưa có trên API Gateway | 🟠 | Chưa có route | Người có quyền CDK | `cdk deploy VvaRestApiStack` |
| 3 | Test `integration` ghi vào DB production | 🟠 | Đã xảy ra 03/10 | Tri (hạ tầng test) | Bắt buộc biến DSN riêng cho test |
| 4 | Tắt biểu cảm theo câu trả lời trên prod | 🟡 | Vẫn bật | Owner + Tony xác nhận | Nếu đồng ý thì `cdk deploy VvaAgentStack` |
| 5 | Grader bản siết (của Nguyen) chưa ship | 🟡 | `release` vẫn dùng bản lỏng | Owner duyệt | Test chung trước khi ship |

Trạng thái deploy kèm theo:
- Lambda `vva-agent` vẫn chạy image `53ca80c`. SSM `/vva/agent/image-tag` ghi lần cuối 30/09 21:20.
- Lần push `release` ở `0b634ca` (Tri, khoảng 20:59) chưa tới Lambda. Cần xem run `deploy-agent` trên
  GitHub Actions: còn đang chạy hay đã fail.

---

## 1. 🟠 Một tool lỗi làm hỏng cả lượt chat

### Hiện trạng
- `agenticRAG/langgraph_agents/graph.py:184`: `ToolNode(all_tools)` không truyền `handle_tool_errors`.
- Mọi tool trong `tools/pgvector_tool.py` đều ghi log rồi `raise` khi lỗi: `kb_search` (dòng 155),
  `memory_search` (281), `resume_last_session` (409), `recall_self` mới thêm (540).
- `langgraph` 1.2.4 (bản đang cài ở máy Nguyen) dùng `_default_handle_tool_errors`. Hàm này chỉ đổi
  `ToolInvocationError` (sai tham số) thành `ToolMessage`, còn **mọi lỗi khác thì ném tiếp**. Các bản
  `langgraph` cũ mặc định bắt mọi lỗi tool.
- `requirements-langgraph.txt:14` và `agenticRAG/requirements-agent-runtime.txt:35` ghi
  `langgraph>=0.2.0`. Image Lambda lấy bản mới nhất lúc build, nên hành vi khi tool lỗi phụ thuộc
  vào ngày build. **Chưa kiểm** bản nào đang chạy trên Lambda.
- `api/main.py:546`: `async for ... in graph.astream(...)` không có `try/except`, và `event_generator`
  (main.py:357–360) cũng không.

### Hậu quả
Chỉ cần Neon chập chờn một nhịp, model embedding lỗi, hay một bảng chưa migrate (ví dụ
`character_knowledge`), toàn bộ lượt chat sẽ:
- bị cắt ngang luồng SSE, không có event `done`;
- không ghi session;
- frontend hiện thông báo lỗi (`error_stream`) thay vì câu trả lời.

Trong khi đó, thiết kế gốc (D23 "rỗng không phải lỗi") giả định lỗi tool quay về thành `ToolMessage`.
Synthesizer coi nội dung chứa `"error"` là không có nguồn (`_has_tool_results`, synthesizer.py:336)
và chuyển sang nhánh từ chối nhẹ nhàng.

### Đề xuất
1. **Không** dùng `handle_tool_errors=True` trần. Cách đó trả về chuỗi `"Error: ..."`, không chứa
   `"error"` có ngoặc kép, nên synthesizer sẽ coi chuỗi lỗi là **evidence**. Thay vào đó, trả về JSON
   khớp với quy ước sẵn có:

   ```python
   # graph.py
   import json

   def _tool_error_as_result(exc: Exception) -> str:
       logger.warning("tool_failed", extra={"error_type": type(exc).__name__, "error": str(exc)[:200]})
       return json.dumps({"error": type(exc).__name__})

   g.add_node("tools", _make_guarded_tools_node(
       ToolNode(all_tools, handle_tool_errors=_tool_error_as_result)))
   ```

2. Pin `langgraph` vào bản đã test (ví dụ `langgraph>=1.2,<1.3`) ở cả hai file requirements.
3. Test: một tool giả ném lỗi, kiểm graph vẫn chạy tới synthesizer, `_has_tool_results` trả `False`,
   lượt kết thúc bằng event `done`.

Ước lượng: khoảng 30 phút kèm test. File này của Tri, nên Nguyen chưa sửa.

---

## 2. 🟠 Route feedback chưa có trên API Gateway

### Hiện trạng
- Route được khai báo trong `infra/infra/rest_api_stack.py:270–272`
  (`POST`/`DELETE /me/feedback/messages/{message_id}`, commit `242c6e3`, 29/09).
- API `vva-api` (`fy1jccsfx3`) đang chạy: **deploy lần cuối 23/09 10:52**. Các route có liên quan:
  `/chat`, `/me/preferences`, `/motion`, `/motion/{job_id}`. **Không có `/me/feedback/...`**.
- CI (`deploy-agent.yml`) chỉ chạy `update-function-code`, không chạy `cdk deploy`, nên route mới
  không tự lên.

### Hậu quả
Khi frontend có nút like/dislike lên prod (Amplify build từ `release`), mỗi lần bấm sẽ nhận
`403 Missing Authentication Token` từ API Gateway. `ChatMessage.tsx` hoàn tác phiếu (`applyVote`, có
`catch`), nên không lỗi giao diện, nhưng **không phiếu nào được lưu**.

### Đề xuất
- `cdk deploy VvaRestApiStack` (đúng một stack, theo `infra/README.md`: không bao giờ `--all`).
- Sau deploy, thử `POST` với token thật: nhận `200` hoặc `404 message not found`, không phải `403`.
- Migration 012 và 013 đã lên Neon (worklog 01/10 của Tri, mục H3). Persona `anne` đã được sync
  vào DB tối 03/10, trước khi push `release`.

---

## 3. 🟠 Test `integration` ghi vào DB production

### Hiện trạng
- `tests/langgraph_agents/conftest.py:48–65`: `pg_dsn_or_skip` trả về **DSN của chính app**
  (`get_default_dsn()`), và chỉ skip khi DSN là mặc định localhost.
- `shared/env.py:77`: `.env` được nạp với `override=False`.
- Trên máy Nguyen, `.env` có `VVA_PG_DSN` trỏ tới **Neon production** (role `eca_user`).
- Vì vậy chạy `pytest` mà không loại test `integration` là ghi vào prod.

### Sự cố 03/10, khoảng 16:20
- Lúc rà code, Nguyen/K chạy `pytest tests -m "not e2e"`, tức là **quên loại `integration`**.
- Hai test trong `test_crud_pooled_integration.py` đã chạy xong trên endpoint pooled của Neon prod.
  Chúng dùng user giả `00000000-0000-4000-8000-00000000dead`: tạo rồi xoá một dòng `user_memory`
  ("pooled-endpoint probe"), sau đó fixture `_cleanup` xoá `user_memory`, `conversations`, `users`
  của user đó.
- Test thứ ba (30 lần `GET /me/memory`, chỉ đọc) bị dừng giữa chừng.
- **Chưa kiểm** xem còn sót dòng nào không, vì việc đó cần một lệnh đọc prod.
- `test_feedback_routes.py` và `test_rls_policies.py` (cũng dùng DSN) chưa kịp chạy.

### Đề xuất
1. Test DB thật chỉ chạy khi có biến riêng, không bao giờ lấy DSN của app:

   ```python
   @pytest.fixture
   def pg_dsn_or_skip():
       dsn = os.getenv("VVA_TEST_DSN")
       if not dsn:
           pytest.skip("set VVA_TEST_DSN to run live-database tests")
       return dsn
   ```

2. Trong lúc chưa sửa, chỉ chạy test theo cách sau:
   - `-m "not integration and not e2e"`;
   - kèm `VVA_PG_DSN=postgresql://vva:vva_dev@localhost:5433/vva`;
   - và AWS key giả (vì `~/.aws` của Nguyen là quyền admin).
3. Máy Nguyen thiếu `boto3`, `tyro`, `aws_cdk`, `langchain_google_genai`, nên khoảng 90 test chưa bao
   giờ chạy được ở đây. Đây là lý do lỗi grader ở mục 5 lọt qua. Cần cài đủ requirements trong một
   venv riêng.

---

## 4. 🟡 Tắt biểu cảm theo câu trả lời trên prod

### Hiện trạng
- `infra/infra/agent_stack.py:339`: `"VVA_REPLY_EMOTION": "0"` (commit `3943771`, 01/10). Việc này
  **có chủ ý**: nằm trong bước T0 của `docs/plans/agent-context-plan.md:112–114` ("Không xóa code
  emotion, chỉ tắt bằng cờ").
- `shared/reply_emotion.py:63`: mặc định là bật (`"1"`).
- Lambda `vva-agent` lúc 21:04 **không có** biến này, nên emotion vẫn bật. Tri đã ghi điều này vào
  `tech-debt.md`: cần `cdk deploy VvaAgentStack` thì biến mới có hiệu lực. Xoá code là việc riêng,
  cần báo Tony trước.

### Hậu quả khi deploy
Model không còn gắn tag cảm xúc, nên không có event `emotion`. Mặt nhân vật giữ nguyên trong lúc trả
lời: frontend chỉ đổi biểu cảm khi nhận event đó (`ChatContext.tsx:683–688`).

Đây là tính năng đã ship 26/09 (worklog 26/09, mục "Reply-driven avatar emotion"). Slide thuyết trình
COS40006 đang giới thiệu "a face that reacts to every answer".

### Cần quyết
- Owner và Tony xác nhận việc tắt tính năng này trên prod.
- Nếu đồng ý: `cdk deploy VvaAgentStack`, và sửa câu tương ứng trong slide.

---

## 5. 🟡 Grader bản siết (của Nguyen) chưa ship, cần Owner duyệt

### Hiện trạng
- `release` (`0b634ca`) dùng grader của Tri: bản gốc lỏng, cộng các mẫu Task 3/B2/4b. Chính Tri đã
  ghi nợ: "Bộ kiểm cảnh báo nguy hiểm tiếng Việt quá lỏng ... Siết lại là thay đổi về an toàn lâm
  sàng, cần Owner quyết".
- Nhánh `feature/langgraph-rewrite` có bản siết của Nguyen (29/09), đã gộp với thay đổi của Tri
  khi merge 03/10.
  - Đo trên bộ 143 nhãn: tỉ lệ cho qua sai và bỏ sót của 6/8 tag là 0%. Còn lệch 5 nhãn, đều ở
    `exercise_steps` và `motion_descriptor`.
  - Bản siết cũng thêm mẫu tiếng Anh cho bộ kiểm dẫn nguồn và chống chỉ định. Đây là vấn đề (3)
    Tri đo ở V7.
- Merge `4c35254` (đã push) chứa một lỗi của bản siết: 2 test cũ trong
  `test_phase2_5_integration.py` fail.
  - Câu "Ngừng tập ngay lập tức nếu thấy đau" không được tính là cảnh báo.
  - Câu "Không nên tập nếu đau đầu gối" không được tính là chống chỉ định.
  - **Đã sửa** ở máy Nguyen, đã stage nhưng chưa commit. Sau khi sửa: 278 test grader pass, bộ 143
    nhãn vẫn lệch 5.
  - CI chỉ chạy test trên `release`, nên lỗi này chưa làm đỏ CI. Merge vào `release` mà thiếu bản
    sửa thì `deploy-agent.yml` sẽ chặn.

### Cần quyết
- Owner duyệt bản siết, vì đây là thay đổi về an toàn lâm sàng.
- Trước khi ship: commit bản sửa, chạy test chung trên nhánh feature. Theo ghi chú ship của Tri,
  9 commit của nhóm sau `0b634ca` "chưa được test chung".

---

## Đã kiểm, không thấy rủi ro

- **API feedback** (`api/routes_feedback.py`): kiểm chủ sở hữu 2 lớp (RLS và điều kiện `user_id`
  trong SQL); comment không bao giờ được ghi log; giới hạn 1000 ký tự cả ở API lẫn DB.
- **Migration 012/013:** có RLS. `character_knowledge` chỉ cho app quyền `SELECT`, và cần
  `app.character` (gắn ở `/chat`, `main.py:339–340`).
- **`recall_self`:** chặn 2 lớp (SQL `WHERE character_slug` và RLS); chỉ được đưa cho nhân vật có
  `sheet.md`.
- **Config budget/ngưỡng:** đọc `config/langgraph.yaml` đúng đường dẫn trong image Lambda (Dockerfile
  có `COPY config ./config`). Thiếu config thì về giá trị cũ.
- **Frontend:** `tsc` 0 lỗi, eslint sạch, vitest 430/430. Ko-fi chỉ nhận URL `https://ko-fi.com/<tên>`.
  Phiếu feedback hoàn tác khi API lỗi.
- **CI SpeechLLm:** role `GitHubActionsECRRole` đã có quyền `ssm:PutParameter` cho
  `/vva/speechllm/image-tag`.
- **Commit của Du và Tony sau merge:** chỉ thêm log evidence (synthesizer) và đổi UI mobile/camera.

## Trạng thái git của Nguyen (03/10 21:04)

- `feature/langgraph-rewrite` local đang ở `4c35254` (đã push), chậm hơn `origin` 11 commit.
- Đã stage, chưa commit: bản sửa 2 rule grader (`grader.py`) và worklog 03/10.
