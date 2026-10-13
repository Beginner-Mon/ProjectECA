---
tags: [plan, agent, memory, stm, ltm]
status: approved
date: 2026-10-13
---

# Memory v2 — STM, tra lịch sử, LTM, lượt bị dừng giữa chừng

Plan để K giao cho subagent triển khai. K không tự sửa repo; K viết đề bài cho từng subagent, đọc
diff và chạy test sau mỗi việc.

## Context

Tri muốn đổi phần memory theo ý tưởng đã dán ngày 13/10: tách ba loại dữ liệu (lịch sử gốc, STM,
LTM) thay cho cách hiện tại, nơi một bản tóm tắt session vừa là phần nén của STM vừa là trí nhớ
dài hạn. Đọc code còn thấy bốn lỗi độc lập với thiết kế:

- **Fact và tóm tắt không tới model viết câu trả lời.** `nodes/memory.py` gói `[USER FACTS]` và
  `[SESSION HISTORY]` vào một SystemMessage; `nodes/synthesizer.py:659-662` bỏ mọi SystemMessage.
- **Lỗ giữa session.** Lịch sử nguyên văn bị cắt ở ~1000 token; phần bị cắt không nằm trong bản
  tóm tắt nào.
- **Ngưỡng tóm tắt đếm sai đơn vị.** `maybe_summarize` cộng `messages.token_count`, là tổng token
  LLM của cả lượt chứ không phải độ dài tin nhắn, nên "10k" kích thất thường sau vài lượt.
- **Mỗi lượt tốn 2 lần gọi DB vô ích trước khi graph chạy.** Khối "STM warm-up" ở
  `api/main.py:344-367` đọc DB để nạp một cache đang tắt ở prod và ở máy Tri.

Kết quả mong muốn: trong một session agent không quên phần đầu; sang session khác agent tra được
chuyện cũ bằng nguyên văn; agent biết các fact về user (gồm sức khoẻ) ở mọi session; và một lượt
bị dừng giữa chừng luôn để lại trạng thái xác định.

## STM và LTM lưu ở đâu

Tất cả nằm trong Postgres. Ví dụ một session đã có 10 lượt, model đang trả lời lượt 11:

| Dữ liệu | Bảng | Ghi lúc nào | Lượt 11 model thấy |
| --- | --- | --- | --- |
| Lượt 1–10 nguyên văn | `messages` (20 dòng) | câu hỏi: đầu lượt; câu trả lời: cuối lượt | chỉ lượt 7–10 |
| Tóm tắt lượt 1–6 | `summaries` (chỉ của session này) | khi phần chưa tóm tắt vượt ngưỡng | có |
| Plan, kết quả tool của từng lượt | không lưu | — | không |
| Chỉ mục để tra từ session khác | `turn_index` (mới, 1 dòng mỗi lượt) | sau mỗi lượt | không |
| "Mổ gối trái 3 tháng trước" | `user_memory` (theo user) | bước lọc LTM | có, ở mọi session |

"Lưu STM" chỉ là hai việc: ghi dòng `messages` mỗi lượt, và ghi một dòng `summaries` cho phần cũ
khi nó không còn vừa ngân sách. `summaries` là phần đã nén; phần chưa nén là các dòng `messages`.

## Dừng giữa chừng hoạt động thế nào

Server chỉ ghi DB một lần, sau khi graph chạy xong (`api/main.py:749-777`). Mọi kiểu dừng xảy ra
trước thời điểm đó hiện không để lại gì, kể cả câu hỏi.

| Kiểu dừng | Hiện tại | Sau plan |
| --- | --- | --- |
| Bấm Stop, mở chat mới, đổi hội thoại, đóng tab | Trình duyệt ngắt kết nối. Server nhận ra thì dừng graph và không lưu gì; không nhận ra thì chạy hết và lưu. Prod 30 ngày: chưa lần nào nhận ra | Khi client ngắt, server dừng lượt ngay. Câu hỏi luôn được lưu. Câu trả lời đã hoàn chỉnh vẫn được lưu; câu trả lời bị cắt lúc đang ghi được lưu tới đoạn đã gửi và đánh dấu chưa xong; chưa có chữ nào thì chỉ có câu hỏi |
| Rớt mạng | Như trên. Frontend hiện dòng lỗi, không tải lại | Như trên: câu hỏi luôn được lưu, câu trả lời hoàn chỉnh vẫn được lưu, phần bị cắt được lưu và đánh dấu chưa xong. Frontend tự tải lại câu trả lời đã lưu |
| Lambda hết giờ, hết RAM, crash | Mất cả lượt, không dấu vết | Câu hỏi còn trong DB. Câu trả lời mất; user gửi lại thì chạy lại từ đầu (~9 s) |
| LLM lỗi bên trong một node | Graph tự xử lý qua `error_handler`, lượt vẫn kết thúc và lưu | Không đổi |
| Đứt sau khi đã lưu (đang phát giọng) | Câu trả lời đã lưu; giọng bị cắt | Không đổi; nghe lại bằng nút loa |

Không có checkpoint nên trường hợp server chết thật phải chạy lại cả lượt. Checkpoint chỉ bớt
được planner và retriever (vài giây); synthesizer vẫn viết lại từ đầu. Khi client ngắt, server dừng lượt ngay;
trên Lambda invocation có thể kết thúc cùng lúc, nên việc ghi câu trả lời có sống sót hay không còn phải đo (việc A1b).

## Quyết định

| # | Quyết định | Ai |
| --- | --- | --- |
| 1 | LTM lưu cả fact sức khoẻ của user | Tri, 13/10 |
| 2 | Xoá hội thoại thì xoá fact rút từ nó | Tri, 13/10 |
| 3 | Bước lọc LTM chạy **khi hội thoại kết thúc**. Chạy sau mỗi lượt là một công tắc đang tắt; chỉ bật nếu số đo cho thấy đáng | Tri, 13/10 |
| 4 | Model BYOK cửa sổ nhỏ vẫn hiển thị | Tri, 13/10 |
| 5 | Không thêm LangGraph checkpointer | K đề xuất |
| 6 | `summaries` chỉ còn vai STM. Tra chuyện cũ chuyển sang `turn_index` | K đề xuất |
| 7 | Bản đầu chưa có: full-text, tầng topical, dormant, consolidation, job định kỳ | K đề xuất |

Đảo D20: fact và tóm tắt chuyển sang field riêng trong state, vì đường đi qua `messages` chính là
chỗ chúng bị bỏ.

## Quy tắc cho mọi subagent

- Làm trên nhánh `feature/memory-v2`. Không checkout, merge hay push `release`. Không push gì cả.
- Commit theo đúng path của việc mình làm; không `git add -A` (repo có file chưa track cần giữ
  nguyên: `docs/architecture/aws-layered.drawio`, `.claude/CLAUDE.md` đang sửa dở).
- Không chạy `alembic upgrade` trên bất kỳ DB nào. Chỉ viết file migration.
- Không sửa file dưới `SpeechLLm/` (kích deploy TTS khi release).
- Python: `C:\Miniconda\envs\firstconda\python.exe`. Chạy lẻ vài file test thì thêm
  `-W "ignore::PendingDeprecationWarning"`. Lỗi sẵn có, bỏ qua:
  `test_phase6_circuit_breaker::test_planner_degrades_when_no_provider_can_be_built`.
- Frontend: kiểm bằng `npm run build` và `npm test`, không dùng `tsc --noEmit`.
- Viết test trước cho hành vi mới. Sửa đúng phạm vi việc được giao; thấy lỗi ngoài phạm vi thì
  báo lại, không sửa.
- Ghi việc đã làm, kết quả test và chỗ khác plan vào `docs/worklogs/DD-MM-YYYY-memory-v2.md`.
- Báo cáo cuối: file đã đổi, lệnh test đã chạy kèm kết quả thật, điều chưa làm được.

## Đợt A — chạy ngay sau khi duyệt (không cần DB riêng)

**A0. Cắt nhánh và chép plan** · Haiku
`git checkout -b feature/memory-v2` từ `feature/langgraph-rewrite`; chép plan này vào
`docs/plans/memory-v2.md`; commit.

**A1. Role `memory` trong `llm.py`** · Sonnet · sau A0
Role mới chỉ chạy key ECA (`_ECA_ONLY_ROLES`), thinking tắt (`_ROLE_EXTRA_BODY`), timeout 15 s,
không retry, temperature 0. Test: user BYOK vẫn nhận model ECA cho role này.

**A1b. Thử dừng giữa chừng trên prod** · Tri làm, K đọc log
Gửi một câu rồi (a) tắt mạng, (b) bấm Stop, mỗi kiểu một lần. K xem log `vva-agent` có
`chat_complete` và dòng `messages` không. Kết quả ghi vào worklog; không chặn các việc khác.

**A2. Probe bước lọc LTM** · Sonnet · sau A1 · song song A3, A4
`local_tests/run_memory_probe.py` và `memory_probes.yaml`, theo mẫu `run_context_probe.py`. 20
session tổng hợp viết tay (tiếng Việt và tiếng Anh; có session không có fact nào, có session agent
gợi ý điều user không nói). Gọi LLM thật bằng role `memory`, không đụng DB. Chạy hai kiểu đầu vào:
từng lượt và cả session. Ghi `docs/tracking/memory-probe-V0.md` và `.json`: thời gian p50/p95, số
fact đúng / sai / bịa, tỉ lệ "không có gì mới".

**A3. Probe tra lịch sử** · Sonnet · sau A0
Script đo e5-small trên cặp hỏi–đáp nguyên văn, không đụng DB: câu hỏi nhắc lại (Việt và Anh) có
đưa đúng cặp vào top 3 không, điểm của câu ngoài lề nằm ở đâu. Thử cả cách chấm theo câu gốc của
user (`GREATEST`, như `db1fce2`). Đề xuất giá trị `memory_min_similarity` mới.

**A4. Sửa STM (P1)** · Opus · sau A1
- `state.py`: thêm `user_facts: list[str]`, `session_summary: list[str]`.
- `nodes/memory.py`: trả hai field đó thay cho SystemMessage. Lịch sử = mọi tin nhắn sau
  `covers_up_to_seq` mới nhất, trong một câu SQL, vẫn chạy song song với hai lần đọc kia; có trần
  an toàn, vượt trần thì bỏ phần cũ nhất và log `history_overflow_dropped`. Không đọc cache STM.
- `api/main.py:344-367`: bỏ khối STM warm-up. `shared/stm.py` và `_append_stm` giữ nguyên.
- `nodes/summarizer.py::maybe_summarize`: đếm bằng `len(content) // 4`, không dùng `token_count`.
  Khi phần chưa tóm tắt vượt `history_compact_at` (mặc định 2 × `history`): tóm tắt phần cũ nhất,
  giữ nguyên văn phần mới nhất vừa `history`. Chunk vẫn append-only. Dùng role `memory`.
- `nodes/synthesizer.py`: hai khối mới cạnh `_build_about_you`, thêm vào `middle_blocks` và hai
  dict `prompt_blocks`. Ngân sách mới `user_facts: 300`, `session_summary: 500` trong
  `shared/context.py` và `config/langgraph.yaml`. Lời dặn kèm khối fact: đây là lời user kể, không
  phải chẩn đoán; tin nhắn hôm nay mâu thuẫn thì theo tin nhắn hôm nay. Khi cắt, giữ fact về hạn
  chế vận động và tình trạng sức khoẻ trước.
- `nodes/planner.py`: thêm fact vào user message; context hint chỉ lấy tin user/agent.
- Test: fact trong state có mặt trong prompt của synthesizer và planner; session 12 lượt không
  còn lượt nào nằm ngoài cả lịch sử lẫn tóm tắt; cập nhật `test_phase2_5_memory.py`,
  `test_memory_regression.py`, `test_pr2_summarizer.py`, `test_a1_a2_a3.py`.

**A5. Lượt bị dừng — backend (P5)** · Opus · sau A4
- `db/session_store.py`: tách `write_session_turn` thành ghi câu hỏi (đầu lượt, kèm upsert `users`
  và `conversations`, id do Python sinh) và ghi câu trả lời (cuối lượt).
- `api/main.py`: ghi câu hỏi bằng một task chạy song song với graph, không chặn token đầu; chờ nó
  xong trước khi ghi câu trả lời. Đưa id câu hỏi vào `config["configurable"]`. Bỏ `return` sớm ở
  dòng 625-627. Trong `finally`: huỷ `housekeeping` nếu chưa xong.
- `nodes/memory.py`: lịch sử loại trừ câu hỏi của lượt đang chạy.
- Test: graph ném lỗi thì dòng câu hỏi vẫn có; prompt của synthesizer không lặp câu hỏi; cập nhật
  `test_write_session_turn_batching.py`, `test_phase5_sse.py`.

**A6. Lượt bị dừng — frontend (P5)** · Sonnet · sau A0 · song song với backend
- `lib/api.ts::consumeSSE`: stream đứt sau khi đã phát sự kiện thì không phát lại từ đầu.
- `contexts/ChatContext.tsx`: sau lỗi stream (không phải abort), gọi `getSession` ở 3 s, 10 s,
  30 s; có câu trả lời đã lưu thì thay dòng báo lỗi. Làm lại khi trình duyệt báo có mạng trở lại.
  Nút Stop giữ nguyên hành vi.
- Test vitest cho hai hành vi trên.

**A7. Ngân sách theo model của user (P6)** · Sonnet · backend sau A4, frontend sau A0
- Frontend: `modelCatalog.ts::toMeta` đọc `limit.context`; gửi `context_window` trong `llm` chỉ khi
  `/llm/config` báo backend nhận field này (`LLMChoice` đang `extra="forbid"`). Không lọc model.
- Backend: `LLMChoice.context_window` (tuỳ chọn), `RequestLLM.context_window`;
  `shared/context.py::context_budget` nhân `history`, `evidence`, `session_summary` theo tỉ lệ cửa
  sổ so với 10k, kẹp trong khoảng 0,3–4. Tràn thì cơ chế ECA trả lời thay đã có sẽ xử lý.

## Đợt B — cần Tri: Neon branch để phát triển, và báo Nguyen

**B1. Tra chuyện cũ bằng lịch sử gốc (P2)** · Opus · sau A4 và A3
Cần bảng chỉ mục vì tra theo nghĩa là so vector, mà `messages` không có vector. Chi phí: một lần
embed mỗi lượt, không gọi LLM, chạy sau khi câu trả lời đã gửi.
- Migration `014_turn_index`: `session_id` (FK `conversations`, cascade), `user_message_id` và
  `assistant_message_id` (FK `messages`, cascade, unique), `content`, `embedding vector(384)`,
  `turn_at`. HNSW `vector_cosine_ops`; policy RLS qua `conversations` như `messages` (mẫu
  `007_rls.py`); `GRANT` riêng cho `eca_user`; một lệnh mỗi `op.execute`.
- `nodes/summarizer.py::housekeep`: bước `index_new_turns` — lượt của user chưa có chỉ mục, embed
  theo lô, insert `ON CONFLICT DO NOTHING`, giới hạn số lượt mỗi lần (tự lấp dần session cũ).
- `tools/pgvector_tool.py::memory_search`: giữ tên, mô tả, tham số; đổi nguồn sang `turn_index`;
  giữ chấm theo câu gốc của user, ngưỡng tối thiểu (số của A3), ngày = ngày của lượt đó. Trả đoạn
  trích kèm tiêu đề session và ngày.
- `evidence.py`: kết quả memory thành chữ thay vì JSON thô.
- Cờ `memory.history_index_enabled`, mặc định tắt.
- Test: trả đúng cặp, không trả session đang chat, user khác không đọc được, xoá hội thoại làm mất
  dòng chỉ mục; cập nhật `test_memory_search_recall.py`.

## Đợt C — cần Tri xem số của A2 và chấp nhận

**C1. LTM (P3)** · Opus · sau B1
Chạy khi hội thoại kết thúc: Lambda nhận ra điều đó lúc user gửi tin ở hội thoại khác, và bước
lọc chạy trong request đó sau khi câu trả lời đã gửi. Fact của hội thoại trước vì vậy có từ tin
thứ hai của hội thoại mới.
- Migration `015_user_memory_ltm`: `user_memory` thêm `source` (`user` | `extracted`),
  `source_session_id` (FK `conversations`, ON DELETE CASCADE), `updated_at`; `conversations` thêm
  `ltm_up_to_seq`.
- `nodes/summarizer.py`: `extract_user_facts` thay `summarize_finished_sessions`. Một lời gọi role
  `memory`, JSON: đọc tin chưa lọc của một session và danh sách fact hiện có, trả thao tác
  ADD / UPDATE / DELETE / NOOP. Áp dụng và cập nhật `ltm_up_to_seq` trong một transaction. Tối đa
  2 session mỗi lượt.
- Chặn bằng code: tối đa 5 thao tác mỗi lần; fact ≤ 300 ký tự; bỏ fact chứa số giấy tờ, thẻ, điện
  thoại, email; không sửa hay xoá fact `source = 'user'`; DELETE là `valid = false`.
- Chỉ ghi điều user tự nói về mình; gợi ý và suy đoán của agent không thành fact.
- Cờ `memory.ltm_enabled` (mặc định tắt) và `memory.ltm_include_current_session` (mặc định tắt).
- Test: thay `test_session_end_summary.py`; fact `source = 'user'` không bị đụng; xoá hội thoại
  làm mất fact của nó.

**C2. Màn hình "ECA nhớ gì về bạn" và chính sách (P4)** · Sonnet · sau C1
- View `memory` trong `ProfileSettingsModal.tsx` (4 chỗ: union, danh sách back, tiêu đề, nội
  dung) và một mục trong `SettingsContent.tsx`, theo mẫu view `legal` của `69feded`. Component mới
  liệt kê, thêm, xoá fact bằng `listUserMemory` / `createUserMemory` / `deleteUserMemory` đã có
  trong `lib/api.ts`. Khoá i18n `memory.*`.
- `api/routes_crud.py::list_user_memory`: trả thêm `source`, `updated_at`. Không đặt việc embed
  vào file này (`test_crud_app.py` chặn).
- `privacy.vi.md`, `privacy.en.md`: bản nháp đoạn về fact được lưu, gồm thông tin sức khoẻ user
  kể; xem và xoá ở đâu; xoá hội thoại thì fact từ đó mất theo. Tri và Nguyen duyệt câu chữ.

## Dùng lại, không viết mới

- `housekeep` và cách chờ sau `done` (`api/main.py:850-857`, Nguyen `0a15a2e`).
- Chấm `GREATEST`, `memory_min_similarity`, ngày theo lượt (Nguyen `db1fce2`).
- `_build_about_you` làm mẫu khối prompt; `recall_self` làm mẫu tool đọc pgvector.
- `get_embedding_service().aembed_passages` để embed theo lô.
- `local_tests/run_context_probe.py` làm mẫu cho hai probe.

## Phối hợp và rủi ro

- **Nguyen.** A4, B1, C1 sửa code và test của `0a15a2e`, `db1fce2`. Tri báo Nguyen trước đợt B.
  Plan này không quyết định lần release BYOK.
- **Fact sai là rủi ro an toàn.** C1 chỉ chạy sau khi Tri chấp nhận số của A2.
- **A5 thêm một lần ghi DB mỗi lượt**, chạy song song nên không chặn token đầu; A4 bớt hai lần
  đọc trước graph.
- **CRUD Lambda** phải deploy tay khi C2 lên prod.
- `db/gdpr.py::delete_message` đang tắt trên prod và gọi DB không qua RLS; plan này không sửa.

## Kiểm chứng (K làm sau mỗi việc)

- Đọc diff so với đề bài; chạy `C:\Miniconda\envs\firstconda\python.exe -m pytest
  tests/langgraph_agents -m unit -q`; frontend `npm run build` và `npm test`.
- Sau A4: chạy `run_context_probe.py` để chắc hành vi trả lời không lùi.
- Sau B1 và C1: chạy lại probe A3 và A2 trên code thật, so với V0; test integration trên Neon branch.
- Sau khi lên prod: Logs Insights trên `/aws/lambda/vva-agent` cho `housekeeping_timeout`, thời
  gian bước lọc và bước đánh chỉ mục, `memory_search_done.top_similarity`.

## Ship (chỉ khi Tri ra lệnh, từng bước riêng)

1. Commit trên `feature/memory-v2`; merge vào `feature/langgraph-rewrite` sau khi K nghiệm thu.
2. Test local.
3. `git push origin feature/langgraph-rewrite`.
4. Cập nhật `release` local từ `origin/release`, merge nhánh feature vào.
5. `git push origin release`.

Không dùng `git push origin HEAD:release`. Trước bước 5: chạy migration trên Neon prod. Sau bước
5: deploy tay CRUD Lambda nếu C2 đi cùng.

## Deployment — chưa nằm trong plan này

- Cách chạy việc ghi memory trên Lambda khi có thêm bước đánh chỉ mục và bước lọc: giữ cách chờ
  sau `done` (tối đa 30 s) hay tách sang invocation riêng. Cần số của A2.
- Job định kỳ quét hội thoại đã im, để fact có sẵn từ tin đầu tiên.
- RAM và thời gian của `vva-agent` (2048 MB, 300 s) khi mỗi lượt embed nhiều hơn.
- Chi phí lời gọi LLM thêm bằng key ECA.
- Thứ tự bật cờ trên prod và cách quay lui (tắt cờ là đủ; hai migration chỉ thêm).
- Lấp chỉ mục cho hội thoại cũ: lấp dần mỗi lượt, hay chạy một lần cho hết.
