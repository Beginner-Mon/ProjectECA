# Plan: Message Feedback (👍/👎 + lý do) — v1

**Ngày:** 29/09/2026 — K · **Branch:** `feature/feedback-system` (cắt từ `feature/langgraph-rewrite`)
**Người thực thi:** subagents (K điều phối + review, không tự code)

**Phạm vi v1 (user chốt 29/09):** CHỈ luồng feedback trên từng câu trả lời. Góp ý về app trong
Settings chuyển sang **Phase 2** (§7). Thiết kế cooldown/rate-limit của nó đã ghi sẵn ở đó để khỏi mất.

**Đã chốt với user:**
- Xoá hội thoại hoặc tài khoản → feedback xoá theo (cascade).
- Box dislike là **modal giữa màn hình** (Huỷ + Gửi + ô lý do).
- `message_feedback` **không có cột `user_id`**.

**Duyệt nguyên plan =**
- lưu trên Neon;
- đưa vào v1: A (turn context) và C (**file SQL xem feedback**, user chọn 29/09);
- B để v1.1;
- **D (alarm SNS) đã bỏ** (user 29/09).

Xem §4.

---

## 0. Context

Nút 👍/👎 trên câu trả lời đã có trong UI (`ChatMessage.tsx:128-181`) nhưng **chỉ đổi màu**.
- Code chỉ là hai `useState` cục bộ, không gọi API nào.
- Trạng thái mất khi reload, khi đổi session, và trên desktop cả khi đóng chat panel (panel unmount).
- Chưa có box lý do.

**Mục tiêu:** lưu bền mỗi vote, gắn đúng vào câu trả lời cụ thể, kèm đủ ngữ cảnh để nhóm hành động được: sửa prompt, sửa retrieval, sửa persona, hoặc phát hiện lời khuyên tập luyện không an toàn.

**Lỗ hổng gốc phải lấp trước:** frontend **không biết id thật của message**.

| Chỗ | Hiện trạng |
|---|---|
| DB | Có `messages.id UUID PK` (`alembic/versions/002_m4_fresh_schema.py:69-77`) |
| `write_session_turn` | INSERT không `RETURNING` (`db/session_store.py:452-458`) |
| SSE `session_persisted` | Chỉ gửi `{session_id}` (`api/main.py:619`) |
| History | SELECT không đọc `id` (`session_store.py:291,300`); `_shape_message` bỏ nó (`:61-75`) |
| Frontend | Tự sinh id: `crypto.randomUUID()` (`ChatContext.tsx:547`), `restored-${i}` (`:310`), `switched-${i}` (`:463`). Id đổi sau mỗi lần reload |

### Thuật ngữ
- **RLS (Row-Level Security):** Postgres tự lọc dòng theo người đang gọi.
  - Mỗi request, backend đặt `app.user_id = <Cognito sub>` trong transaction (`db/postgres.py:399-427`).
  - Từ đó mọi câu SQL trên bảng có policy chỉ thấy và chỉ được ghi dòng của user đó, kể cả khi code quên `WHERE`.
  - `USING` lọc lúc đọc/sửa/xoá. `WITH CHECK` chặn lúc ghi.
- **Upsert:** một câu SQL `INSERT … ON CONFLICT (message_id) DO UPDATE` — chưa có thì thêm, có rồi thì sửa.
  - Frontend không cần biết dòng đã tồn tại hay chưa.
  - Bấm nhanh hai lần không sinh hai dòng.
- **`messages.extras`:** cột JSONB trên `messages` (migration 008), là "túi đồ phụ" cho từng message để khỏi thêm cột.
  - Hôm nay chỉ chứa `{"motion": {"job_id", "prompt"}}`.
  - Plan đề xuất thêm khoá `meta` (§4 mục A).

---

## 1. Spec

### 1.1 Khi nào hiện nút
- **Hiện:** chỉ trên message assistant **đã được lưu** (có `serverId`).
- **Ẩn trên:**
  - lời chào (`GREETING_ID='1'`);
  - bong bóng lỗi stream (`ChatContext.tsx:741-747`);
  - lượt persist thất bại;
  - lúc đang stream.
- Hôm nay nút hiện cả trên lời chào và bong bóng lỗi. Đó là bug, và slice này sửa luôn.

### 1.2 Hành vi

| Trạng thái | User bấm | Kết quả |
|---|---|---|
| trung lập | 👍 | `POST` rating=+1 → xanh |
| 👍 | 👍 | `DELETE` → trung lập |
| 👎 | 👍 | `POST` rating=+1 (xoá reasons/comment cũ) → xanh |
| trung lập / 👍 | 👎 | `POST` rating=−1 **ngay lập tức**, rồi mở **modal lý do** |
| 👎 | 👎 | `DELETE` → trung lập (không mở modal) |

- **Vote 👎 được lưu ngay khi bấm.** Tín hiệu "câu này tệ" là thứ giá trị nhất, và phần lớn user bỏ qua ô lý do.
- **[Huỷ] = đóng modal, giữ vote 👎 không lý do.** Nếu muốn Huỷ = huỷ luôn vote thì chỉ đổi 1 dòng handler; N/Owner quyết lúc duyệt.
- **Optimistic UI:** đổi màu ngay; nếu API lỗi thì hoàn tác và hiện lỗi inline. Không có toast lib; dùng pattern banner đỏ `BillingContent.tsx:31-41,126-131`.
- **Sống qua reload:** history trả `feedback` cho từng message, nên thumb vẫn đúng màu sau reload hoặc đổi session.
- **Không cần rate limit riêng:** mỗi message có tối đa 1 dòng (upsert), nên số dòng bị chặn trên bởi số câu trả lời.

### 1.3 Modal lý do (giữa màn hình, như AvatarPickerModal / Settings)
- **Bố cục:** card giữa màn hình trên cả desktop lẫn mobile.
  - Style `AvatarPickerModal.tsx:30-45`: `max-w-md`, `rounded-2xl`.
  - Header: tiêu đề + nút X. Body. Footer: **[Huỷ] [Gửi]**.
- **Bắt buộc `createPortal(…, document.body)`**, như `ui/confirm-dialog.tsx:28-31,81-82`, với `z-[10001]`. Lý do: ChatPanel nằm trong floating panel có `transform`, nên `fixed inset-0` sẽ bị kẹt trong panel.
- **Chips lý do** (multi-select). Mã lưu bằng tiếng Anh, nhãn đi qua i18n:

  | Mã | Nhãn VI (gợi ý) | Hiện khi |
  |---|---|---|
  | `incorrect` | Thông tin không chính xác | luôn |
  | `unsafe` | Có thể không an toàn / gây chấn thương | luôn — **ưu tiên cao** |
  | `not_relevant` | Không đúng trọng tâm câu hỏi | luôn |
  | `incomplete` | Thiếu thông tin / chưa đủ chi tiết | luôn |
  | `hard_to_follow` | Khó hiểu hoặc quá dài | luôn |
  | `wrong_language` | Trả lời sai ngôn ngữ | luôn |
  | `motion_issue` | Động tác 3D không đúng | chỉ khi message có `motionJobId` |
  | `voice_issue` | Giọng đọc có vấn đề | chỉ khi message có `speech` |
  | `other` | Khác | luôn |

- **Ô lý do:** textarea tuỳ chọn, tối đa **1000 ký tự**, có bộ đếm. Placeholder nhắc *"Đừng ghi thông tin cá nhân nhạy cảm"*.
- **Nút [Gửi]:**
  - Disabled cho đến khi có ≥1 chip hoặc text không rỗng.
  - Đang gửi: hiện spinner.
  - Thành công: đóng modal.
  - Lỗi: giữ modal, giữ nguyên nội dung, hiện banner lỗi.
- **Esc / click overlay / X** đều tương đương [Huỷ].

---

## 2. Kiến trúc

### 2.1 Storage — trade-off Neon vs S3

| Tiêu chí | **Neon (Postgres)** | **S3 (JSON object)** |
|---|---|---|
| Gắn feedback ↔ message | FK thật tới `messages(id)`; DB từ chối id rác | Chỉ là chuỗi trong key/JSON; không gì đảm bảo id tồn tại |
| **Cascade khi xoá** (đã chốt) | `ON DELETE CASCADE`: `DELETE /sessions/{id}` và `delete_user` (`db/gdpr.py:123-144`) tự xoá theo, **0 dòng code** | `gdpr.py` và `routes_crud` DELETE không chạm S3 → phải viết thêm list-by-prefix + delete |
| Phân quyền | RLS có sẵn (`007_rls.py`) | IAM + prefix theo user; CRUD Lambda **chưa có quyền S3 nào** → grant mới |
| Hiện lại 👍/👎 khi load history | 1 `LEFT JOIN` trong query history đang có, **0 round trip thêm** | N lần `GetObject`, hoặc thêm index riêng |
| Toggle / đổi vote | Upsert / `DELETE` | Ghi đè object, không có ràng buộc |
| Thống kê (tỉ lệ 👎 theo persona, theo tuần) | SQL trực tiếp | Athena + Glue, hoặc tải về tự xử lý |
| Hạ tầng thêm | 1 migration + 2 route + 1 resource API GW | Bucket/prefix + lifecycle + IAM + code boto3 mới |
| Chi phí | ≈ 0 (dòng nhỏ; Neon đã thức vì user đang dùng) | ≈ 0 |
| File lớn (ảnh, audio) | ❌ | ✅ |

- **Chọn Neon.** Ba lý do quyết định:
  1. Cascade (đã chốt) chỉ "miễn phí" trên Neon.
  2. Thumb phải sống qua reload, và việc này cần JOIN với history.
  3. Không thêm hạ tầng AWS nào.
- **S3 chỉ cần khi có file đính kèm** (ảnh chụp màn hình ở Phase 2) hoặc để export dataset eval. Khi đó dùng mô hình hybrid, Neon vẫn là nguồn chính.
- **DynamoDB:** cùng nhược điểm như S3 (không FK, không cascade), nên loại.

### 2.2 Data model — migration `012_message_feedback` (`down_revision = "011_character_voices"`)

```sql
-- current-state: 1 dòng / message, không lưu lịch sử đổi vote.
-- KHÔNG có user_id: chủ sở hữu suy ra qua message → conversation → user,
-- giống cách bảng messages tự nó làm (007_rls.py:73-75 OWNED_VIA_SESSION).
CREATE TABLE message_feedback (
  message_id UUID PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  rating     SMALLINT NOT NULL CHECK (rating IN (-1, 1)),
  reasons    TEXT[] NOT NULL DEFAULT '{}',
  comment    TEXT CHECK (comment IS NULL OR char_length(comment) <= 1000),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- **Không có `user_id`:** biết `message_id` là truy ra được user. Thêm cột sẽ tạo hai nguồn sự thật có thể lệch nhau, chỉ để đổi lấy một phép join rẻ (PK lookup). Xoá user cascade dọc theo chuỗi `users → conversations → messages → message_feedback`.
- **`reasons` không có CHECK ở DB.** Validate bằng Pydantic `Literal`, nên thêm một lý do là sửa code chứ không phải viết migration. Cùng triết lý với `messages.extras` (`008_messages_extras.py:21-27`).
- **Không nhét vào `messages.extras`:** trộn dữ liệu do user sửa được vào transcript, không có `updated_at`, không đếm/query sạch được.
- **RLS:**
  - `ENABLE ROW LEVEL SECURITY`.
  - Policy `FOR ALL`, dùng cùng một predicate cho `USING` và `WITH CHECK`. Đây là mẫu `OWNED_VIA_SESSION` (007:135-145) nối thêm một bước:
    `EXISTS (SELECT 1 FROM messages m JOIN conversations c ON c.session_id = m.session_id WHERE m.id = message_feedback.message_id AND m.role = 'assistant' AND c.user_id = current_setting('app.user_id')::uuid)`
  - **Predicate bắt buộc:** FK bỏ qua RLS, nên thiếu nó thì user A vote được lên message của user B.
- **Grant:** `GRANT SELECT, INSERT, UPDATE, DELETE ON "message_feedback" TO "eca_user"`. Phải tường minh vì 007 cố ý không có default privileges (:118-120).
- **Quy tắc migration:** mỗi `op.execute()` đúng một statement; `current_setting` chỉ 1 tham số; `downgrade()` đầy đủ; mẫu tham khảo `009_user_preferences.py:44-90`.

### 2.3 Gắn message ↔ feedback (plumbing id)
1. **`write_session_turn`:** statement INSERT messages thứ hai đổi thành `conn.fetch(… RETURNING id, role)` và trả `assistant_message_id`. **Số round trip không đổi** (docstring `session_store.py:368-382`).
2. **`main.py:619`:** `session_persisted` → `{"session_id", "assistant_message_id"}`.
3. **`load_session_messages`** (`session_store.py:289-306`):
   - Cả 2 nhánh SELECT thêm `m.id`, cộng `LEFT JOIN message_feedback f ON f.message_id = m.id` lấy `f.rating, f.reasons, f.comment`.
   - Cột trong ORDER BY phải qua alias `m.`.
4. **`_shape_message`:**
   - Luôn trả `id`.
   - Trả `feedback: {rating, reasons, comment}` **chỉ khi có**, như motion keys (`:55-60`).
5. **Frontend `Message`** (`ChatMessage.tsx:18-55`):
   - Thêm `serverId?: string` và `feedback?: MessageFeedback | null`.
   - **Giữ `id` client làm React key.** Không thay nó, vì `restored-${i}` và `assistantMsgId` được dùng ở nhiều chỗ.

### 2.4 API — mount ở CRUD Lambda (buffered, KHÔNG phải agent)

| Method + path | Body | Trả về |
|---|---|---|
| `POST /me/feedback/messages/{message_id}` | `{rating: 1\|-1, reasons?: Reason[], comment?: str}` | 200 `{message_id, rating, reasons, comment, updated_at}` · 404 nếu không tồn tại / không phải của mình / không phải assistant · 422 |
| `DELETE /me/feedback/messages/{message_id}` | — | 204 (idempotent) |

- **Dùng POST/DELETE, không PUT/PATCH:** preflight CORS của API GW chỉ cho `GET, POST, DELETE, OPTIONS` (`infra/infra/rest_api_stack.py:138`).
- **Upsert một statement**, qua `pg.fetchrow` (tự bọc transaction + RLS):
  ```sql
  INSERT INTO message_feedback (message_id, rating, reasons, comment)
  SELECT m.id, $2, $3::text[], $4 FROM messages m
  WHERE m.id = $1::uuid AND m.role = 'assistant'
  ON CONFLICT (message_id) DO UPDATE
    SET rating = EXCLUDED.rating, reasons = EXCLUDED.reasons,
        comment = EXCLUDED.comment, updated_at = now()
  RETURNING message_id, rating, reasons, comment, updated_at
  ```
  RLS của `messages` ẩn message người khác, nên 0 dòng → 404.
- **Validation (Pydantic):**
  - `reasons` chỉ hợp lệ khi rating=−1; unique; ≤ 9 phần tử.
  - `comment` được strip; rỗng → `null`; ≤ 1000 ký tự.
  - rating=+1 → server tự đặt `reasons = '{}'` và `comment = NULL`.
- **Logging:** `feedback_message_saved {user_id, message_id, rating, reasons}` và `feedback_message_cleared {user_id, message_id}`. **Tuyệt đối không log `comment`** — dữ liệu sức khoẻ (`docs/tracking/predeploy-audit.md` I6).
- **Router mới `api/routes_feedback.py`:**
  - Mount trong `api/crud_app.py:94-96` (đường deploy) và `api/main.py:199-206` (dev local).
  - Trong main.py phải mount **trước** early-return GDPR ở `main.py:410-415`, nếu không route biến mất.
  - Không import graph/nodes/embedding (`test_crud_app.py` canh).
  - Mẫu route: `api/routes_preferences.py:99-147`.
- **Infra (`rest_api_stack.py`, cạnh block `/me/preferences` :251-260):** thêm `me/feedback/messages/{message_id}` với POST + DELETE, `**authed`, integration `crud`.
  - Nếu quên bước này, API trả **403 "Missing Authentication Token"**. Đã từng xảy ra, xem comment :252-257.

### 2.5 Luồng dữ liệu

```
/chat SSE ─► graph ─► write_session_turn ──RETURNING id──► session_persisted{assistant_message_id}
                                                              │
ChatContext: message.serverId = id ◄──────────────────────────┘
👍/👎 ─► POST|DELETE /me/feedback/messages/{serverId} ─► API GW (Cognito) ─► vva-crud-api ─► Neon message_feedback (RLS)
Reload ─► GET /sessions/{id} ─► messages LEFT JOIN message_feedback ─► {id, feedback} ─► thumb đúng màu
Xoá hội thoại / xoá user ─► FK CASCADE ─► feedback biến mất
```

### 2.6 Privacy & retention
- `comment` là PII-adjacent: không log, không đưa vào metric, và chỉ xem trong Neon console (§4.C).
- **Retention:** giữ đến khi user xoá hội thoại hoặc tài khoản. Thời hạn tối đa (ví dụ 12 tháng) là quyết định của Owner (ASVS V14.2.4/7). Không chặn v1.

---

## 3. Phạm vi — trong / ngoài v1

| Trong v1 | Ngoài v1 |
|---|---|
| Plumbing message id (SSE + history) | Góp ý về app trong Settings + cooldown → §7 |
| Bảng `message_feedback` + RLS + 2 route + CDK | Popup đánh giá |
| 👍/👎 bền qua reload + modal lý do | File đính kèm (S3) |
| Mục A (turn context) + C (file SQL xem feedback) | Mục B → v1.1 · Mục D (alarm SNS) — bỏ |

---

## 4. Các phần bổ sung — giải thích từng cái

**A. Turn context snapshot — đề xuất v1**
- **Là gì:** lúc persist, ghi `extras.meta` vào dòng assistant: `{request_id, persona_id, ui_locale, output_mode, web_search, required_outputs, grader_result, latency_ms}`.
- **Vì sao:** một 👎 thiếu ngữ cảnh chỉ nói "câu này tệ". Không biết persona nào, grader có pass không, lượt đó chậm không. Tệ nhất là **không tra được CloudWatch**: mọi log line có `request_id`, nhưng `request_id` hiện bị vứt sau event `done` (`main.py:270,705`). Thêm nữa, `grader_result` được truyền vào `write_session_turn` nhưng **chưa bao giờ được ghi**.
- **Chi phí:** ~200 byte/dòng, **0 round trip thêm** (cùng câu INSERT). Mọi dữ liệu có sẵn tại `main.py:607`, không sửa node nào.
- **Effort:** S (~1h, gộp vào T2).
- **Rủi ro:** ≈ 0. `_shape_message` bỏ qua khoá lạ, nên frontend không thấy `meta`.

**B. Retrieval sources snapshot — đề xuất v1.1**
- **Là gì:** `extras.meta.sources = [{document_title, chunk_index, similarity}]` lấy từ kết quả `kb_search`.
- **Vì sao:** phần lớn 👎 "không chính xác / thiếu" của RAG là **lỗi retrieval** (sai tài liệu, similarity thấp) chứ không phải lỗi viết câu trả lời. Không có sources thì không phân biệt được hai loại.
- **Chi phí:** ~0.5 KB/dòng. Phải bắt update của node `tools` trong vòng stream: `main.py:562` hiện bỏ qua node ngoài `_STAGE_NODES`; cần thêm nhánh giống kimodo `:514-560`, rồi parse theo `tools/pgvector_tool.py:84-91`.
- **Effort:** M (~2–3h).
- **Rủi ro:** parse trong vòng stream, nên phải phòng thủ bằng try/except như kimodo.

**C. File SQL để xem feedback — v1 (user chọn 29/09, thay cho Python script)**
- **Là gì:** `docs/ops/feedback-queries.sql` gồm 3 query viết sẵn. Copy vào SQL editor của Neon console để chạy.
  1. **Tổng quan N ngày:** số 👍/👎 và tỉ lệ 👎, chia theo persona (`extras->'meta'->>'persona_id'`).
  2. **Đếm theo lý do:** `unnest(reasons)` → số lần mỗi mã, `unsafe` đứng đầu.
  3. **Chi tiết các 👎**, mới nhất trước:
     - thời gian, persona, reasons, comment;
     - **câu hỏi của user**: dòng `role='user'` cùng session có `seq_id` lớn nhất nhỏ hơn `seq_id` của câu trả lời;
     - câu trả lời, `request_id`, `grader_result`.
- **Vì sao:** chưa có admin UI. Câu hỏi và câu trả lời nằm ở hai dòng khác nhau, viết join tay mỗi lần rất vụng.
- **Chi phí:** 0 code, 0 hạ tầng.
- **Về RLS:** console chạy bằng role owner, và 007 không `FORCE RLS`, nên owner đọc được mọi dòng.
- **Privacy:** dữ liệu sức khoẻ chỉ hiện trên trình duyệt, không ghi ra đĩa.
- **Effort:** S (~30–45 phút).
- **Tham số:** số ngày để ở đầu mỗi query dạng `interval '7 days'`, có comment chỉ chỗ sửa.
- **Phụ thuộc:** đọc `extras.meta` từ mục A; lượt cũ không có `meta` thì hiện NULL, không lỗi.

**D. ~~Alarm SNS cho lý do `unsafe`~~ — BỎ (user 29/09)**
- Mã lý do `unsafe` vẫn giữ trong danh sách chip.
- Các feedback `unsafe` được xem qua mục C.

---

## 5. Implementation plan cho subagents

### 5.0 Quy tắc chung (nhét nguyên văn vào prompt mỗi subagent)
- Làm trong worktree `../VVA-feedback`. **Cấm** `git restore`, `git checkout --`, `git reset`, `git stash`, `git clean`. Không đụng `release`. Subagent **không commit**; coordinator (Haiku) commit sau mỗi wave.
- **Python test:** `pytest <path> -W ignore::PendingDeprecationWarning`, interpreter `C:\Miniconda\envs\firstconda\python.exe`. Không import third-party ở module scope trong `tests/conftest.py`.
- **Frontend check:** `npm run build` (tsc -b), `npx vitest run`, `npm run lint`. **Không dùng `tsc --noEmit`**, vì lệnh này luôn pass.
- **Không thêm npm dependency nào.** Lockfile đang bị pin toolchain.
- **Chuỗi UI:** mọi chuỗi đi qua i18n (`en.json` + `vi.json`). ESLint `i18next/no-literal-string` và `catalog.test.ts` canh việc này. Nhãn trong object phải build bên trong component (`SettingsContent.tsx:125-133`).
- **Comment code:** tiếng Anh, mật độ giống code xung quanh. **Không bao giờ log `comment`.**
- **Worklog:** người thực hiện ghi `docs/worklogs/DD-MM-YYYY.md`, đúng tác giả. K không viết worklog.

### Wave 0 — chuẩn bị (Haiku)
- **T0:**
  1. `git worktree add ../VVA-feedback -b feature/feedback-system feature/langgraph-rewrite`.
  2. Kiểm tra base bằng `git rev-list --left-right --count`.
  3. Chạy `npm ci` trong `ECA_UI/frontend` của worktree, Node 22.18.0.
- **Lý do dùng worktree:** cây hiện tại có thay đổi chưa commit của việc khác (`MobileChatDock.tsx`, `MobileMotionChips.tsx`, worklog 26-09).

### Wave 1 — song song, file rời nhau
**T1 — Migration 012 (Sonnet)**
- **Files:** `alembic/versions/012_message_feedback.py`, `tests/infra/test_message_feedback_migration.py`.
- **Nội dung:** đúng §2.2.
- **Test** (pattern `tests/infra/test_messages_extras_column.py` + `tests/langgraph_agents/test_rls_policies.py:27-45`):
  - chain + single head;
  - một statement mỗi execute;
  - ENABLE RLS + policy có predicate join `conversations`;
  - GRANT;
  - downgrade đối xứng.
- **KHÔNG apply lên Neon.**

**T2 — Plumbing id + turn meta (Sonnet)**
- **Files:** `db/session_store.py`, `api/main.py:602-621`.
- **Nội dung:** §2.3 bước 1–4, cộng mục A: tham số `meta: dict | None` cho `write_session_turn`, trộn vào `extras` cạnh `motion`.
- **Test:**
  - sửa `_FakeConn` trong `tests/langgraph_agents/test_write_session_turn_batching.py:37-90` để hỗ trợ `fetch` + RETURNING;
  - `_shape_message` có `id`, và có `feedback` chỉ khi có;
  - payload `session_persisted` có `assistant_message_id`;
  - `extras` chứa `meta`.
- **Grep mọi caller** của `write_session_turn`.

**T3 — Frontend plumbing (Sonnet)**
- **Files:** `lib/api.ts`, interface `Message` trong `components/ChatMessage.tsx:18-55`, `i18n/locales/en.json` + `vi.json`.
- **Nội dung:**
  - Types `FeedbackReason`, `MessageFeedback`.
  - `SessionMessage` thêm `id` + `feedback?`.
  - `saveMessageFeedback` và `clearMessageFeedback` dùng instance `http` (`api.ts:98`).
  - Toàn bộ key `feedback.*` (tiêu đề modal, 9 lý do, placeholder, nút, lỗi).

### Wave 2 — song song (sau khi Wave 1 được commit)
**T4 — Routes (Sonnet)**
- **Files:** `api/routes_feedback.py` (mới), mount trong `api/crud_app.py` + `api/main.py`.
- **Nội dung:** §2.4.
- **Test** `tests/langgraph_agents/test_feedback_routes.py` (pattern `test_preferences.py:20-55`: `auth_env`, `override_user`, `_mock_pg`):
  - 422 khi `reasons` đi kèm +1;
  - 404 khi upsert trả 0 dòng;
  - DELETE idempotent;
  - log không chứa `comment`.
- **Integration test** với `app_dsn_or_skip` (`test_rls_policies.py:142-170`): user B → message của A → 404.
- `test_crud_app.py` phải vẫn xanh.

**T5 — CDK route (Sonnet)**
- **File:** `infra/infra/rest_api_stack.py`.
- **Test:** thêm assertion vào file test CDK hiện có của rest API stack trong `tests/infra/`, kiểm resource + POST/DELETE + authorizer COGNITO.
- **Không deploy.**

**T6 — UI (Sonnet; nâng Opus nếu ChatContext tỏ ra rối)**
- **Files:** `lib/turnLifecycle.ts` + `.test.ts`, `contexts/ChatContext.tsx`, `hooks/useChat.ts`, `components/ChatMessage.tsx` (`AssistantActions`), `components/feedback/DislikeFeedbackModal.tsx` (mới).
- **Nội dung:**
  - `turnLifecycle`: trong `session_persisted`, nếu `data.assistant_message_id` là string thì gọi hook mới `attachServerId(id)`. Hook này **không** gate theo `isCurrent` (lý do ở `turnLifecycle.ts:66-68`).
  - `ChatContext`:
    - gán `serverId` cho `assistantMsgId`;
    - map `serverId: m.id, feedback: m.feedback ?? null` ở `:310` và `:463`;
    - expose `setMessageFeedback(clientId, fb | null)`.
  - `AssistantActions`:
    - bỏ 2 `useState` local, đọc từ `message`;
    - ẩn thumb khi không có `serverId`;
    - optimistic + hoàn tác theo §1.2.
  - Modal theo §1.3.
- **Test:** `turnLifecycle.test.ts` phủ 3 case — có `assistant_message_id`, không có, và backend cũ không gửi field này.

### Wave 3 — sau Wave 2 (T7 thuộc v1; T8 chỉ làm khi duyệt v1.1)
- **T7 (Sonnet)** — mục C: `docs/ops/feedback-queries.sql`, 3 query theo §4.C, mỗi query có comment tiếng Anh ngắn nói nó trả lời câu hỏi gì.
  - Không có test tự động.
  - **Verify:** K chạy cả 3 query read-only trên Neon sau bước E2E, và kết quả phải khớp các dòng vừa tạo.
  - Query 3 phải trả đúng câu hỏi ngay trước câu trả lời, kể cả trong session có nhiều lượt.
- **T8 (Sonnet)** — mục B: nhánh bắt `tools` trong `main.py` → `meta.sources`. Test gồm cả payload hỏng.

### Wave 4 — K review
- K đọc diff theo §2 và chạy §6.
- Chạy skill `code-review` trên branch.
- Soi riêng 3 điểm: RLS chéo user, không log `comment`, route mount trước GDPR early-return.

### Deploy — cần go-ahead riêng, KHÔNG thuộc task subagent
1. **Migration trước.**
   - Chạy `cd agenticRAG/langgraph_agents && alembic upgrade head` bằng `VVA_PG_DSN_OWNER`.
   - Neon dùng chung cho local và prod. Migration chỉ thêm bảng mới.
   - Kiểm tra `\d message_feedback`, `pg_policies`, `information_schema.role_table_grants`.
   - ⚠️ Code T2 có `LEFT JOIN message_feedback`. Deploy code trước migration thì **GET /sessions/{id} vỡ**, kể cả local.
2. Push `HEAD:release` → `deploy-agent.yml` build image agent.
3. Build `crud_api.zip` → `infra/deploy.ps1` (`VvaCrudApiStack` → `VvaRestApiStack`).
4. Amplify build frontend từ `release`.

Frontend tương thích ngược: backend cũ không gửi `assistant_message_id` → thumb ẩn, không lỗi.

---

## 6. Verification

**Test tự động**
- `pytest tests/infra/test_message_feedback_migration.py tests/langgraph_agents/test_feedback_routes.py tests/langgraph_agents/test_write_session_turn_batching.py tests/langgraph_agents/test_crud_app.py tests/langgraph_agents/test_rls_policies.py -W ignore::PendingDeprecationWarning`, cộng file test CDK của rest API.
- `cd ECA_UI/frontend && npm run build && npx vitest run && npm run lint`.

**E2E local** (uvicorn `:8000`, `STM_BACKEND=none`, frontend dev; migration đã apply)
1. Gửi tin nhắn. Trong DevTools → EventStream, `session_persisted` có `assistant_message_id`.
2. Bấm 👍 → `message_feedback` có dòng rating=1. Reload → thumb vẫn xanh.
3. Bấm 👎 → dòng thành −1 **trước khi** modal mở. Chọn 2 chip + text → Gửi → `reasons`/`comment` được cập nhật.
4. Bấm 👎 lần nữa → dòng bị xoá. Bấm 👎 rồi Huỷ → vote −1 còn, `reasons` rỗng.
5. Lời chào và bong bóng lỗi → không có thumb.
6. Xoá hội thoại → dòng `message_feedback` biến mất (cascade).
7. Gọi `POST /me/feedback/messages/<id của user khác>` bằng token user B → 404.
8. `messages.extras` của lượt mới có `meta.request_id` khớp log line của lượt đó.
9. Grep log: không có nội dung `comment`.

**Sau deploy**
- Preflight `OPTIONS /v1/me/feedback/messages/<id>` trả 200 kèm CORS header.
- 👍 từ app thật trả 200, không phải 403.

---

## 7. Phase 2 (làm sau) — Góp ý về app trong Settings

Ghi lại để không mất. **Không implement trong v1.**

**Spec**
- **Điểm vào:** card "Góp ý về ứng dụng" trong `SettingsContent.tsx:134-140`, mở view drill-down `'feedback'` theo pattern `LanguageSelectionView`.
- **Form:**
  - loại (`bug`, `idea`, `content`, `avatar_voice`, `other`);
  - sao 1–5 (tuỳ chọn);
  - nội dung 1–4000 ký tự;
  - checkbox đính kèm thông tin kỹ thuật (mặc định bật);
  - checkbox cho phép liên hệ lại (mặc định tắt).
- **Popup đánh giá tương lai:** dùng chung bảng, cột `source` (`'settings'` | `'prompt'`).

**Bảng `app_feedback`** (migration riêng, 013)
- Cột: `id`, `user_id` (CẦN, vì không gắn message), `source`, `category`, `rating`, `comment`, `allow_contact`, `context JSONB`, `created_at`.
- RLS append-only: SELECT + INSERT own, không UPDATE/DELETE.
- POST phải upsert `users` trong cùng CTE, vì user có thể chưa chat lần nào (lập luận an toàn ở `session_store.py:406-413`).

**Rate limit / cooldown (user yêu cầu 29/09)**
- **Server là nguồn sự thật.** `POST /me/feedback/app` là một câu INSERT có điều kiện:
  `INSERT … SELECT … WHERE NOT EXISTS (SELECT 1 FROM app_feedback WHERE user_id = $1 AND created_at > now() - make_interval(days => $cooldown))`
  0 dòng → **429** `{next_allowed_at}`.
- `GET /me/feedback/app` → `{last_submitted_at, next_allowed_at}`. Khi mở view, UI hiện *"Bạn đã góp ý ngày X, có thể gửi tiếp từ ngày Y"* thay vì form. User không phải gõ xong mới bị chặn.
- **Cấu hình:** độ dài cooldown đọc từ env `APP_FEEDBACK_COOLDOWN_DAYS`, đổi không cần sửa code.
- **Race chấp nhận được:** hai request song song trong vài ms có thể cùng lọt, tệ nhất là 2 dòng. Frontend đã khoá nút khi đang gửi, và stage throttle 5 rps chặn trên. Không cần lock.
- **Câu hỏi mở cho Phase 2:**
  - Cooldown **7 ngày** (K đề xuất) hay **30 ngày**?
  - Loại `bug` dùng chung cooldown, hay có luật riêng nhẹ hơn (ví dụ tối đa 3 báo lỗi/ngày) để lỗi khẩn không phải đợi hết cooldown?
- **Cần thêm lúc làm:** build info (`__APP_VERSION__` qua `vite.config.ts` define, `Capacitor.getPlatform()`). v1 không cần.

---

## 8. Việc mở cho Owner (không chặn v1)
- Thời hạn lưu feedback tối đa (retention).

**Phát hiện ngoài phạm vi (báo N, không sửa ở đây):** `PATCH /me/preferences` đang tồn tại, nhưng preflight
CORS của API GW không cho PATCH (`rest_api_stack.py:138`). Cần kiểm tra đồng bộ preferences có đang
âm thầm fail trên bản deploy không.
