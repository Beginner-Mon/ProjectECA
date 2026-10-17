---
tags: [plan, agent, documents, pdf, ocr, rag, s3, privacy]
status: draft
date: 2026-10-13
related: "[[scrum-45-image-input]]"
---

# Tài liệu riêng của user: upload PDF → OCR → chia đoạn → agent tìm được

**Mục tiêu:** user upload PDF (phác đồ bác sĩ đưa, giấy ra viện, bài tập của nhà trị liệu). Agent
tìm trong đó ở **mọi hội thoại sau**, trả lời có ghi rõ "theo tài liệu X, trang N". Chỉ chủ
tài liệu đọc được.

**Anh chốt (13/10):** KB riêng của từng user, có trang quản lý (xem, xoá) · trang scan đọc bằng
Gemini 2.5 Flash · tối đa 20 MB, 50 trang mỗi file.

**Hiện trạng:** chưa có. Upload PDF chỉ có ở bản Streamlit cũ (`agentic_rag_gemini`), mất khi
xoá thư mục đó ngày 10/08. `documents` / `kb_embeddings` là KB chung, không có `user_id`, app chỉ
được đọc (007_rls). Nút đính kèm trên FE chỉ nhận ảnh.

## Kiến trúc

```
FE   Settings → "Tài liệu của tôi" → chọn PDF
     → POST /me/documents {filename, bytes} (CRUD) → tạo hàng status=uploaded + presigned POST
     → upload thẳng lên S3: docs/<user_id>/<doc_id>.pdf   (vượt giới hạn 10 MB của API GW)
S3   ObjectCreated → Lambda vva-doc-ingest (async, retry 2, on-failure → hàng status=failed)
       1. pypdf mở file; mã hoá / > 50 trang → failed (lý do rõ cho user)
       2. trang nào có lớp chữ (≥ 30 ký tự) → lấy chữ bằng pypdf
          trang không có chữ (scan, ảnh) → gom thành 1 PDF nhỏ → Gemini 2.5 Flash chép lại
          từng trang (markdown, giữ tiếng Việt có dấu)
       3. chia đoạn ~350 token, gối 50, cắt theo đoạn văn/tiêu đề; mỗi đoạn nhớ trang đầu–cuối
          (e5-small cắt ở 512 token, đoạn dài hơn thì phần đuôi mất khỏi vector)
       4. e5 "passage:" → user_document_chunks; status=ready, số trang, số đoạn, trang OCR
agent  tool mới my_documents_search(query): chỉ tài liệu của user đang đăng nhập (SQL + RLS)
       → evidence "Tài liệu của bạn: <tên file>, tr. N" → synthesizer
xoá  DELETE /me/documents/{id} → xoá hàng (chunk theo CASCADE) + object S3; xoá user → xoá hết
```

**Lambda ingest dùng lại image agent** (có sẵn e5 ONNX, `langgraph_agents`, LWA): function
riêng `vva-doc-ingest`, 3008 MB, timeout 300 s, env `ECA_DOC_INGEST=1`. LWA chuyển event
không phải HTTP vào `POST /events` (`AWS_LWA_PASS_THROUGH_PATH`); app chỉ mở route đó khi
có biến này. Không cần pipeline build mới; CI build image agent như hiện nay. Không chạy trong
Lambda agent vì agent đã dùng ~93% RAM.

## Ràng buộc

- **Tài liệu là dữ liệu, không phải lệnh.** PDF có thể chứa "bỏ qua hướng dẫn trước". Evidence
  từ tài liệu user được đóng khung là nội dung do user cung cấp; synthesizer chỉ trích dẫn, không
  làm theo chỉ dẫn trong đó. Câu an toàn vẫn do code viết (grader-contract); tài liệu nói ngược
  lời khuyên an toàn thì vẫn khuyên hỏi bác sĩ của họ.
- **Không chẩn đoán từ tài liệu.** Agent nói "tài liệu của bạn ghi …", không diễn giải kết quả
  xét nghiệm hay đổi phác đồ.
- **Chỉ chủ tài liệu đọc được.** Bảng có `user_id` + RLS `current_setting('app.user_id')` (như
  007). Lambda ingest ghi bằng role app, bind `app.user_id` lấy từ key S3, nên chỉ ghi được cho
  đúng user đó (`WITH CHECK`). Key ngoài prefix `docs/<uid>/` → bỏ qua, log cảnh báo.
- **Ngưỡng similarity** `doc_min_similarity` trong config, đo bằng bộ thử (phần 6), không đoán.
  Chấm theo cả query của retriever và câu gốc của user, như `memory_search` (`db1fce2`).
- **Không log nội dung**: chỉ `doc_id`, số trang, số trang OCR, số đoạn, thời gian, lỗi.
- **Gemini lỗi/quá hạn** → trang đó bỏ trống, status `ready` kèm `ocr_failed_pages`; FE báo
  "N trang không đọc được". Hạn tổng bằng `asyncio.wait_for`.
- Bucket dùng chung với SCRUM-45 (block public, SSE-S3, chỉ HTTPS, CORS chỉ origin app);
  presigned **POST** có `content-length-range` ≤ 20 MB; chỉ `application/pdf`.
- Test cách ly prod (memory `feedback_tests_hit_prod`); S3 bằng moto; Gemini giả.

## Bản đồ file (dự kiến)

| File | Việc |
| --- | --- |
| `alembic/versions/014_user_documents.py` | `user_documents`, `user_document_chunks` (+ `user_id`, RLS owner, index `user_id`) |
| `api/routes_documents.py` (CRUD) | `POST /me/documents`, `GET /me/documents`, `DELETE /me/documents/{id}` |
| `ingest/user_documents.py` (mới) | đọc PDF, OCR, chia đoạn, embed, ghi; dùng được cả ngoài Lambda để test |
| `api/main.py` | `POST /events` chỉ khi `ECA_DOC_INGEST=1` |
| `tools/pgvector_tool.py` | `my_documents_search` |
| `nodes/retriever_agent.py`, `sources.py` | thêm tool + nhãn nguồn "tài liệu của bạn" |
| `nodes/synthesizer.py` | khối evidence tài liệu user, khung "dữ liệu không phải lệnh" |
| `db/gdpr.py` | xoá object S3 khi xoá tài liệu / user |
| `infra/infra/doc_ingest_stack.py` (mới) | function `vva-doc-ingest` (image agent), S3 trigger, quyền S3 + SSM |
| `infra/infra/crud_api_stack.py`, `rest_api_stack.py` | env bucket, quyền S3, 3 route mới |
| `agenticRAG/requirements*.txt` | `pypdf` (BSD) |
| `ECA_UI/frontend/src/…` | (cùng Tri) view "Tài liệu của tôi" trong Settings: upload, danh sách + trạng thái, xoá |
| `src/legal/privacy.*.md` | thêm: PDF lưu ở S3/Neon tới khi user xoá; trang scan gửi Google Gemini để đọc |

## Các phần (mỗi phần một commit, test riêng)

0. **Bucket** — dùng phần 1 của SCRUM-45. Làm trước, chung một lần.
1. **Schema + RLS** (~0,5 ngày). Test: user A không thấy/không ghi được chunk của user B; xoá
   tài liệu xoá chunk; xoá user xoá hết.
2. **Route CRUD** (~0,5 ngày). Test moto: key đúng prefix, chặn > 20 MB và content-type khác
   PDF, list chỉ của mình, xoá cả object S3.
3. **Ingest** (~1,5 ngày). Test: PDF có chữ → không gọi Gemini; PDF scan → gọi Gemini đúng các
   trang trống; trộn cả hai; mã hoá / 51 trang → failed với lý do; đoạn không vượt 512 token
   e5; trang đầu–cuối đúng; key lạ bị bỏ qua.
4. **Tool + agent** (~1 ngày). Test: chỉ trả tài liệu của user; dưới ngưỡng không trả; evidence
   có tên file + trang; PDF chứa "ignore previous instructions" không đổi hành vi (synthesizer
   giả); grader vẫn thêm câu an toàn.
5. **Frontend** (~1 ngày, cùng Tri). Upload, trạng thái (đang xử lý / sẵn sàng / lỗi), xoá; vitest.
6. **Đo + deploy** (~0,5–1 ngày). Bộ 6–8 PDF thật (có chữ EN/VI, scan tiếng Việt, ảnh chụp
   điện thoại, bảng) + 20–30 câu hỏi có đáp án → recall@5, đặt `doc_min_similarity`, đọc soát
   OCR. Cập nhật chính sách quyền riêng tư. Deploy `DocIngest`, `CrudApi`, `RestApi` (cần anh
   ra lệnh); agent theo release.

**Tổng:** ~4–5 ngày cho N, cộng ~1 ngày FE.

## Chi phí ước lượng

- Gemini 2.5 Flash: PDF 50 trang scan toàn bộ ≈ 13k token vào + ~25k token ra → **≈ $0,07/file**
  trường hợp xấu nhất; file có lớp chữ: $0.
- Lambda ingest: ~30–60 s × 3 GB mỗi file scan ≈ $0,003. S3 + Neon: không đáng kể ở quy mô đồ án.

## Câu hỏi còn mở

1. **Giữ file PDF gốc trên S3** tới khi user xoá (để xử lý lại khi đổi model/chunking), hay xoá
   ngay sau khi đọc xong, chỉ giữ chữ + vector? Đề xuất: **giữ tới khi user xoá**.
2. Giới hạn số tài liệu mỗi user? Đề xuất **20 file**.
3. FE: Tri làm hay N làm, Tri duyệt? (cùng câu hỏi với SCRUM-45)
4. Jira: tạo story mới (vd. "As a user, I want to upload my own PDF documents") hay gộp vào
   SCRUM-45?
