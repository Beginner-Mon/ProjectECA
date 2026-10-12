---
tags: [plan, agent, vision, s3, privacy]
status: draft
date: 2026-10-07
jira: SCRUM-45
---

# SCRUM-45 — Người dùng gửi ảnh trong chat

**Mục tiêu:** Người dùng đính kèm ảnh vào câu hỏi. Agent nhận ra ảnh là **cơ thể/tư thế** hay
**giấy tờ bài tập**, dùng nội dung ảnh để trả lời an toàn (không chẩn đoán; thấy dấu hiệu nguy
hiểm thì khuyên đi khám), và ảnh được lưu riêng tư để xem lại trong lịch sử.

**Anh chốt (07/10):** cả hai loại ảnh · Gemini 2.5 Flash đọc ảnh · lưu ảnh trong S3 riêng tư.

**Hiện trạng:** FE đã có nút đính kèm (tối đa 10 ảnh, có xem trước) nhưng `handleSend` huỷ
object URL, ảnh không đi đâu (`ChatContext.tsx:815`). Backend chưa có trường ảnh nào
(`api/schemas.py::ChatRequest`). DeepSeek không đọc ảnh.

## Kiến trúc

```
FE  chọn ảnh → thu nhỏ (≤1600 px, JPEG 0.85) → POST /me/uploads (CRUD) → presigned POST lên S3
    → POST /chat { query, image_keys: [...] }
agent  memory → vision (mới, chỉ khi có image_keys) → planner → … (như cũ)
       vision: đọc ảnh từ S3 → Gemini 2.5 Flash, JSON
               {kind: body|document|other, description, visible_signs[], text}
               → state.image_context
       planner + synthesizer nhận khối "## What the user's photo shows"
lưu    messages.extras (hàng user) = {"images": [{key, kind, description}]}
xem    GET /sessions/{id} trả URL xem ảnh (presigned GET, 5 phút)
xoá    xoá tin / hội thoại / user → xoá object S3; lifecycle tự hết hạn sau N ngày
```

## Ràng buộc

- **Không chẩn đoán từ ảnh.** Ảnh chỉ thêm ngữ cảnh. Câu an toàn vẫn do code viết (grader-contract);
  thấy dấu hiệu nguy hiểm trong ảnh là tín hiệu cho planner thêm `red_flag_screen` (D33).
- **Vision lỗi không làm hỏng lượt:** Gemini lỗi/quá hạn → lượt vẫn trả lời và nói rõ không đọc
  được ảnh; log `vision_failed`. Hạn tổng cho cả lời gọi bằng `asyncio.wait_for` (bài học từ
  LLM chấm, 05/10), các ảnh gọi song song.
- **Ảnh thuộc đúng người:** key dạng `uploads/<user_id>/<uuid>.jpg`; agent và CRUD từ chối key
  ngoài prefix của user đang đăng nhập (400), kiểm trước khi đọc S3.
- **Không log ảnh hay mô tả đầy đủ:** chỉ log `kind`, số ảnh, độ dài mô tả, thời gian.
- **Bucket:** block public, SSE-S3, chỉ HTTPS, lifecycle N ngày, CORS chỉ cho origin của app.
  Giới hạn kích thước bằng presigned **POST** (`content-length-range`), vì presigned PUT không
  chặn được size.
- **Tối đa 3 ảnh/tin, 5 MB/ảnh**, JPEG/PNG/WebP (FE đổi HEIC sang JPEG khi thu nhỏ).
- Không đổi layout frontend của Tri; phần FE làm cùng Tri.
- Test chạy cách ly prod (memory `feedback_tests_hit_prod`); S3 bằng moto; Gemini giả.

## Bản đồ file (dự kiến)

| File | Việc |
| --- | --- |
| `infra/infra/uploads_stack.py` (mới) | bucket `vva-user-uploads-<account>`, lifecycle, CORS, SSL-only |
| `infra/app.py` | đăng ký `VvaUploadsStack`; tên bucket truyền dạng chuỗi (không export) |
| `infra/infra/crud_api_stack.py` | env `UPLOADS_BUCKET`; Put/Get/Delete trên `uploads/*` |
| `infra/infra/agent_stack.py` | env `UPLOADS_BUCKET`; GetObject trên `uploads/*` |
| `infra/infra/rest_api_stack.py` | `POST /me/uploads` → CRUD (có auth) |
| `api/routes_uploads.py` (mới, CRUD) | cấp key + presigned POST (5 phút) |
| `api/schemas.py` | `ChatRequest.image_keys: list[str]` (≤ 3) |
| `nodes/vision.py` (mới) | node vision |
| `llm.py` | role `vision` → `gemini-2.5-flash` |
| `graph.py`, `routing.py` | memory → vision (khi có ảnh) → planner |
| `nodes/planner.py`, `nodes/synthesizer.py` | khối ngữ cảnh ảnh |
| `db/session_store.py` | lưu `images` vào extras hàng user |
| `api/routes_crud.py` | resume session trả URL xem ảnh |
| `db/gdpr.py` | xoá object S3 khi xoá tin / hội thoại / user |
| `ECA_UI/frontend/src/…` | (cùng Tri) thu nhỏ, upload, gửi key, hiện ảnh |

## Các phần (mỗi phần một commit, test riêng)

1. **Bucket + `POST /me/uploads`** (~0,5 ngày). Test: CDK (block public, SSE, SSL-only,
   lifecycle, CORS); moto: key đúng prefix của user, chặn content-type lạ, policy có
   `content-length-range`.
2. **Vision + `/chat` nhận `image_keys`** (~1,5 ngày). Test: key của user khác → 400; Gemini giả
   trả `body`/`document` → planner và synthesizer nhận khối ảnh; Gemini lỗi hoặc quá hạn → lượt vẫn
   xong, không chuỗi lỗi lạ; stage event `vision` phát trước planner.
3. **Lưu, xem lại, xoá** (~1 ngày). Test moto: extras có `images`; resume trả URL; xoá tin /
   hội thoại / user xoá đúng object, không đụng object người khác.
4. **Frontend** (~1 ngày, cùng Tri). Thu nhỏ + upload + gửi key; ảnh trong bong bóng và khi
   khôi phục hội thoại; vitest.
5. **Đo + quyền riêng tư + deploy** (~1 ngày). Bộ 20–30 ảnh có nhãn (cơ thể bình thường / có dấu
   hiệu nguy hiểm / giấy tờ / ảnh khác) → đo phân loại và tỉ lệ bắt dấu hiệu; câu privacy notice
   (PRIV-01); deploy `VvaUploadsStack`, `VvaCrudApiStack`, `VvaRestApiStack` (cần anh ra lệnh),
   agent theo release.

## Câu hỏi còn mở

1. Giữ ảnh bao lâu? Đề xuất **30 ngày** (lifecycle), xoá ngay khi người dùng xoá hội thoại.
2. Phần frontend: Tri làm, hay N làm và Tri duyệt?
