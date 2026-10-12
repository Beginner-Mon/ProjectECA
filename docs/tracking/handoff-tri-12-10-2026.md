---
date: 2026-10-12
tags: [handoff, release, memory, retrieval, legal, questions]
from: Nguyen
to: Tri
---

# Gửi Tri — việc của Nguyen 05–12/10: cần release + vài câu hỏi

**Tóm tắt:** 4 commit code trên `feature/langgraph-rewrite` chưa lên `release`. Hai trong số đó
sửa lỗi người dùng thấy được: memory dài hạn không chạy, và lần tìm KB đầu tiên chậm ~9 s. Tri
cần (1) xem lại 4 file của Tri mà tôi có sửa, (2) release, (3) trả lời các câu hỏi ở mục 5.

## 1. Chờ release

| Commit | Gì | Người dùng thấy gì |
| --- | --- | --- |
| `3f14d04` | Nạp model e5 lúc khởi động Lambda (`EMBEDDING_WARMUP=1`), log thời gian từng bước của `kb_search` | Lần tìm KB đầu tiên trong mỗi container: trung vị 9,3 s → gần 0 (log prod 20/09–03/10; các lần sau vốn 0,05 s) |
| `0a15a2e` | Tóm tắt các hội thoại đã kết thúc, chạy **trong** request | Agent nhớ được hội thoại trước. Hiện trên prod: 26 `summarizer_trigger_failed` vs 5 `summarizer_done` từ 01/09, không hội thoại nào chạm ngưỡng 10k token |
| `db1fce2` | `memory_search` chấm điểm theo cả câu gốc của người dùng, có ngưỡng 0,84, ngày = ngày diễn ra hội thoại | Nhớ đúng khi người dùng viết tiếng Việt (summary đúng đứng đầu 10/10, trước đây 1/7); "bạn là ai" không còn kéo summary cũ vào |
| `69feded` | Trang Điều khoản + Chính sách quyền riêng tư (SCRUM-41) | Settings → "Terms & Privacy"; `/terms`, `/privacy` mở được khi chưa đăng nhập; một dòng đồng ý dưới nút đăng ký |

Kiểm đã chạy: backend `tests/langgraph_agents` 1165 pass (cách ly prod); frontend vitest
547 pass, `tsc` và `eslint` sạch, `vite build` qua; chạy `memory_search` trên Postgres local;
xem `/privacy` và trang đăng ký bằng playwright.

## 2. Thay đổi hành vi Tri nên biết

- **Stream `/chat` mở lâu hơn sau `done`.** Việc tóm tắt (`summarizer.housekeep`) bắt đầu cùng
  lượt chat, song song với graph, và được `await` **sau** `encode_event("done")`, tối đa 30 s
  (`_HOUSEKEEPING_TIMEOUT_S`). Lý do: Lambda đóng băng container ngay sau response, nên task
  nền cũ chạy tiếp trên kết nối DB đã chết. FE vẫn ổn: nút gửi nhả ở `session_persisted`
  (`turnLifecycle`), giọng nói phát xong trước `done`. Chi phí: mỗi lượt có thể dài thêm vài
  giây thời gian Lambda; timeout 300 s còn dư.
- **Mỗi lượt tóm tắt tối đa 2 hội thoại khác** của user (còn ≥ 4 tin chưa tóm tắt, mới nhất
  trước), cộng hội thoại hiện tại nếu vượt 10k token.
- **Tóm tắt dùng key của ECA, kể cả với user BYOK.** `housekeep` gọi `bind_request_llm(None)`
  trong context riêng của task; request vẫn giữ key của user (có test). Xem câu hỏi 2.
- **Người dùng gửi tin mới khi stream cũ chưa đóng** → FE abort stream cũ → tóm tắt đang chạy
  bị huỷ. Không mất gì: lượt sau tóm tắt lại, vì việc chọn hội thoại dựa trên
  `covers_up_to_seq`.
- **Ngưỡng mới trong config:** `langgraph.retrieval.memory_min_similarity: 0.84` (đọc giống
  `kb_min_similarity`). Đo trên bộ dữ liệu tổng hợp, chưa có số thật. Log
  `memory_search_done` giờ có `top_similarity` và `dropped_below_min` để chỉnh lại.

## 3. File của Tri tôi đã sửa — nhờ Tri xem

| File | Sửa |
| --- | --- |
| `agenticRAG/Dockerfile` | 1 dòng `ENV EMBEDDING_WARMUP=1` cạnh `E5_ONNX_DIR`: chỉ image agent bật, test/local không đổi |
| `tests/langgraph_agents/test_byok_chat.py` | patch `housekeep` thay cho `maybe_summarize` (cơ chế mới) |
| `ECA_UI/frontend/src/pages/CreateAccountPage.tsx` | 1 dòng chữ "Khi tạo tài khoản, bạn đồng ý với…" dưới nút, link mở tab mới. Không đổi layout |
| `SettingsContent.tsx`, `ProfileSettingsModal.tsx` | Nút "Terms" có sẵn (chưa gắn gì) → mở view `legal`. Nhãn đổi thành "Terms & Privacy" |

## 4. Kiểm sau release (tôi làm, chỉ đọc log)

Logs Insights trên `/aws/lambda/vva-agent`:

```
filter msg = "embedding_warm_done" | stats count(), pct(elapsed_ms, 50) as p50_ms
filter msg = "kb_search_done" | stats pct(embed_ms, 50), pct(embed_ms, 95), pct(db_ms, 50)
filter msg in ["summarizer_done", "summarizer_fatal", "housekeeping_timeout", "finished_session_summary_failed"] | stats count() by msg
filter msg = "memory_search_done" | fields top_similarity, results, dropped_below_min
```

Đạt khi: `embed_ms` p95 < 200 ms; `summarizer_done` tăng, 0 `housekeeping_timeout`;
`top_similarity` của các lần gọi thật nằm đúng phía so với 0,84.

## 5. Câu hỏi

**Cần trả lời trước khi release:**

1. **Lịch release.** Tri định release lúc nào? Cần tôi chạy probe hay kiểm gì trước không?
2. **Tóm tắt của user BYOK chạy bằng key ECA.** Có hợp với ADR-007 không? Key của user chỉ
   sống trong một request, nên tóm tắt hội thoại cũ (chạy ở lượt sau) không dùng được key đó.
   Chi phí: tối đa 2 lời gọi `planner` ngắn mỗi lượt, và chỉ khi có hội thoại chưa tóm tắt.
   Nếu không được thì phương án khác: bỏ qua tóm tắt cho user BYOK.
3. **Email liên hệ trong chính sách** đang là `contact@ordinary.studio` (lấy từ About Us).
   Hộp thư này có ai đọc không? Chính sách hứa trả lời trong 30 ngày, nên phải là hộp có người
   đọc. Chỉ cần sửa `LEGAL_CONTACT_EMAIL` trong `src/legal/index.ts`.

**Cần nhóm quyết:**

4. **Log Lambda giữ 731 ngày** (cả 6 log group `vva-*`), trong đó `chat_start` ghi 120 ký tự đầu
   câu chat, tức dữ liệu sức khoẻ. Người dùng xoá hội thoại thì nội dung vẫn còn trong log 2
   năm. Đề xuất giảm còn 30–90 ngày (sửa CDK). Chính sách hiện ghi đúng "tối đa 2 năm";
   nhóm đổi thì tôi sửa cả hai.
5. **SCRUM-45 (ảnh trong chat)**, kế hoạch ở `docs/plans/scrum-45-image-input.md`:
   - Giữ ảnh bao lâu? Đề xuất 30 ngày.
   - Phần FE do Tri làm, hay tôi làm và Tri review?
6. **Integration test đang dùng DSN của app**, nên `.env` local trỏ Neon prod thì test ghi
   lên prod (đã xảy ra 03/10, test tự dọn). Đề xuất: integration test bắt buộc biến riêng
   `ECA_TEST_PG_DSN` và từ chối host `neon.tech`. Sửa `tests/langgraph_agents/conftest.py`,
   là code dùng chung. Tri đồng ý không?

**Khi rảnh:**

7. **SNS `vva-alerts`** (alarm khi LLM chấm lỗi ≥ 3 lần/giờ) chưa có ai đăng ký nhận. Ai nhận?
8. **SCRUM-53 (flash vs reasoning)** không có mô tả. Ý Tri là người dùng chọn chế độ, hay
   trả lời nhanh rồi bổ sung sau? Ai làm?
9. **Thông báo trước khi dùng mic (R-T8):** chính sách đã ghi việc trình duyệt gửi âm thanh
   cho Google/Microsoft/Apple, nhưng chưa có thông báo trước lần dùng đầu. Phần này thuộc FE
   của Tri; Tri làm hay để tôi?
10. **Jira:** SCRUM-52 (BYOK) đã xong nhưng còn ở To Do; SCRUM-13 (Deploy TTS) đã chạy trên
    prod từ 23/09. Tri cập nhật giúp nhé (ticket của Tri, tôi không đụng).

## 6. Chưa làm, ghi lại để khỏi quên

- `rebuild_dirty_chunk` (route xoá một tin nhắn) vẫn là task nền, có thể bị Lambda đóng băng
  giống summarizer cũ. Chưa sửa vì `ENABLE_GDPR_ROUTES` đang tắt trên prod.
- `user_memory` (fact người dùng tự nhập) có API nhưng không có UI, nên chính sách không nhắc tới.
