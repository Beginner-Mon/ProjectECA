---
tags: [plan, agent, context, grader, sse]
status: deferred
date: 2026-10-03
related: "[[agent-context-round3]]"
---

# Backlog — thiết kế lại grader và luồng retry (hoãn)

> **Trạng thái: HOÃN.** Tri quyết ngày 03/10/2026: phần lớn các vấn đề dưới đây đến
> từ thiết kế ban đầu của grader; sửa cho đúng là một việc lớn, làm riêng sau. Đợt
> ship hiện tại chấp nhận các vấn đề này. **Không thực thi file này** cho tới khi
> Tri mở lại; khi mở lại, coi nó là đầu vào cho một bản thiết kế grader mới, không
> phải danh sách vá.

**Repo:** `Virtual-Verbal-Assistant` · **Nhánh:** `feature/agent-context` (HEAD `fbf13411`,
đã có trên `origin`) · **Ngày:** 03/10/2026

Quy tắc làm việc và môi trường: như `docs/plans/agent-context-round3.md`, mục "Quy tắc".

## Context

Vòng 3 làm đúng spec. Số đo V7 và việc đọc lại code cho thấy năm vấn đề còn lại.
Hai vấn đề đầu có từ trước nhánh này; nhánh này làm chúng lộ ra ở tiếng Anh.

1. **Giao diện không bao giờ nhận câu trả lời đã qua grader.** Backend chỉ gửi văn
   bản bằng sự kiện `token` từ synthesizer (`api/main.py`). Trình duyệt ghép các
   token lại (`ECA_UI/frontend/src/contexts/ChatContext.tsx`, nhánh `type === 'token'`:
   `msg.content + content`) và không nhận gì khác. Hệ quả:
   - Câu an toàn do grader chèn và dòng "chưa được kiểm chứng" chỉ có trong DB và
     trong giọng nói; người dùng không thấy trong lượt chat đang diễn ra.
   - Khi grader retry, bản nháp thứ hai được nối tiếp sau bản nháp đầu trong cùng
     một bong bóng chat.
2. **Retry của grader không sửa được gì.** `grader_feedback` chỉ được
   `nodes/retriever_agent.py` đọc, mà mọi tool retriever yêu cầu ở lượt retry bị bỏ
   vì chạm trần vòng (`routing.py`). Synthesizer viết lại mà không biết thiếu gì.
   V7: 7 lượt retry, cả 7 vẫn thiếu sau retry, kết thúc `pass_with_warning`, thời
   gian trung bình 20,2 giây so với 10,5 giây.
3. **Bộ kiểm chất lượng yếu ở tiếng Anh.** `_has_source` đòi các chữ
   source / reference / according to; `_has_contraindication` đòi do not / avoid /
   caution, không nhận "don't", "stop if". V7: 15 lượt tiếng Anh có tag, 6 lượt
   retry, 8 lượt bị nối dòng "This information has not been verified…" dù câu trả
   lời có ghi NHS hoặc thư viện ECA. Tiếng Việt: 16 lượt, 1 retry.
4. **`exercise_protocol` vẫn retry khi câu trả lời trung thực.** `d5_en`, `d5_vi`:
   "the library doesn't give a set count… I won't invent one" bị retry, vì evidence
   của lượt có số của các bài lân cận.
5. **Còn số tự đặt ở lượt không có tag `exercise_protocol`.** V7 nhóm (c): 4/18
   lượt, ví dụ `c1_vi` "Một việc làm ngay: … làm 10 lần", `c5_vi` "bắt đầu ở 3 hiệp
   15 giây". Dòng "Do not supply numbers of your own" chỉ có khi tag đó có mặt.
   Ngưỡng vòng 3 chỉ đo nhóm (d) nên không bắt được.

Kết quả cần đạt: người dùng thấy đúng câu trả lời mà grader đã duyệt; retry sửa
được phần thiếu và tốn một lời gọi LLM thay vì hai; lượt tiếng Anh không bị retry
oan; không còn số liều lượng ngoài evidence ở bất kỳ nhóm nào.

## Thứ tự

```
1 UI nhận câu trả lời đã chấm → 2 retry có phản hồi → 3 bộ kiểm tiếng Anh
→ 4 exercise_protocol → 5 số chỉ từ evidence → 6 bộ đo → đo V8 → báo cáo
```

---

## Task 1 — Giao diện hiển thị câu trả lời đã qua grader

**File:** `agenticRAG/langgraph_agents/api/main.py` (`_stream_chat`),
`ECA_UI/frontend/src/contexts/ChatContext.tsx`, một file thuần mới trong
`ECA_UI/frontend/src/lib/`, `tests/langgraph_agents/test_phase5_sse.py`.

**Backend**

- Theo dõi văn bản đã stream trong lượt (nối các `token`).
- Khi synthesizer bắt đầu stream **lần thứ hai** trong cùng lượt: gửi sự kiện
  `answer_reset` (payload rỗng) trước token đầu tiên của lần đó, và xóa bộ đệm.
- Khi graph kết thúc, nếu `final_answer` khác văn bản đã stream: gửi
  `answer_final` với `{"content": final_answer}`, trước `session_persisted` và `done`.
- Không gửi `answer_final` khi hai bên giống nhau.

**Frontend**

- `answer_reset`: xóa nội dung bong bóng của lượt và biến `answer`.
- `answer_final`: thay nội dung bong bóng bằng `content`.
- Sự kiện lạ vẫn bị bỏ qua như hiện tại, nên backend mới chạy được với frontend cũ.

**Test**

- SSE: lượt có retry → đúng một `answer_reset`, nằm giữa hai chuỗi token.
- SSE: grader chèn câu an toàn → `answer_final` chứa câu đó và đứng trước `done`.
- SSE: lượt `pass` không retry → không có `answer_reset`, không có `answer_final`.
- Frontend (vitest, hàm thuần): chuỗi sự kiện token, reset, token, final cho ra
  đúng nội dung cuối.

## Task 2 — Retry nhận được phản hồi của grader

**File:** `nodes/synthesizer.py`, `routing.py`, `graph.py`, `nodes/grader.py`.

- `route_after_grader`: kết quả `retry` đi thẳng tới `synthesizer`, không qua
  `retriever_agent`. Cập nhật bảng cạnh trong `graph.py`.
- `synthesizer_node`: khi `state["retry_count"] >= 1` và có `grader_feedback`, chèn
  một khối ngay trước task:

  ```
  ## Revise your previous draft
  Your previous draft was missing the following. Write the answer again and
  include them:
  {grader_feedback}
  ```

- `TAG_RULES["exercise_protocol"]` trong `grader.py`: đổi chuỗi phản hồi thành
  `State the sets, reps and frequency the evidence gives, with the source. If the
  evidence gives none for this exercise, say so.` (chuỗi hiện tại kèm ví dụ số
  "3 hiệp × 10 lần").
- `retriever_agent.py`: bỏ phần đọc `grader_feedback` và `{retry_note}` nếu sau
  thay đổi này không còn đường nào dẫn tới nó; nếu còn thì giữ.

**Test**

- Graph với LLM giả: lượt retry chạy `retriever_agent` đúng 1 lần (lần đầu),
  `synthesizer` đúng 2 lần.
- Prompt của lần synthesizer thứ hai chứa khối "Revise your previous draft" và
  chuỗi phản hồi của tag bị thiếu; lần đầu không chứa.
- Trần retry vẫn là 1.

## Task 3 — Bộ kiểm chất lượng nhận ra câu trả lời tiếng Anh

**File:** `nodes/grader.py`, `tests/langgraph_agents/test_phase2_5_grader.py`.

Viết test trước, dùng câu trả lời thật trong `docs/tracking/context-probe-V7.json`
làm dữ liệu.

**`evidence_citation`**

- Thêm kiểm tra dựa trên evidence của lượt: câu trả lời đạt nếu nó nhắc tới tên
  nguồn hoặc tên tài liệu có trong evidence lượt đó. Tên nguồn lấy từ
  `KB_SOURCE_TYPE_LABELS` và nhãn trong `sources.py`; so khớp bằng từ khóa riêng
  của nhãn (ví dụ "ECA", "NHS") và bằng `document_title`.
- Giữ các mẫu hiện có của `_has_source`; kết quả là "đạt nếu một trong hai đạt".
- Dương tính: câu trả lời V7 của `d1_en`, `c6_en`, `c7_en`, `c0_1_en` phải đạt với
  evidence của chính lượt đó (trường `evidence_text` trong JSON).
- Âm tính: một câu trả lời không nhắc nguồn nào, với cùng evidence, phải trượt.

**`contraindication`**

- Thêm mẫu tiếng Anh cho: `don't`, `shouldn't`, `should not`, `stop if`, `not if you`,
  `get (it) checked / cleared before`.
- Dương tính: câu trả lời V7 của `c5_en`, `d2_en`, `d3_en` phải đạt.
- Âm tính: `Don't worry, this one is easy.` · `I don't have that in the library.`
  phải trượt. Nếu không thể vừa đạt bảng dương vừa trượt bảng âm, **dừng lại báo Tri**.

Không đổi các mẫu tiếng Việt. Không đổi bộ kiểm an toàn.

## Task 4 — `exercise_protocol`: câu "nguồn không ghi" được tính là đạt

**File:** `nodes/grader.py`.

- Tag này đạt khi câu trả lời có phần số mà evidence có (luật vòng 3), **hoặc** khi
  câu trả lời nói rõ nguồn không ghi số cho bài được hỏi.
- Mẫu cho vế thứ hai, hai ngôn ngữ, ví dụ: "doesn't / does not give / list / state /
  carry / specify … set / rep / number / count", "no sets, no reps", "không ghi số",
  "không ghi số hiệp", "không có số hiệp".
- Dương tính: câu trả lời V7 của `d5_en`, `d5_vi`, `d2_vi`.
- Âm tính: `I don't count reps when I train.` · `Mình không đếm số lần đâu.`

## Task 5 — Số liều lượng chỉ lấy từ evidence, ở mọi lượt

**File:** `nodes/synthesizer.py`.

- Thêm một dòng vào phần hướng dẫn chung của `_SYNTHESIZE_TASK` (không gắn với tag):

  ```
  - Numbers for sets, reps, hold times or frequency come only from the evidence.
    If the evidence gives none, give none.
  ```

- Thêm đúng dòng đó vào `_REFUSE_TASK`.
- Không sửa persona. Ghi nhận trong worklog: luật "still give the user one thing they
  can do right now" trong `anne/_core.md` là nơi các con số tự đặt ở V7 xuất phát;
  nếu sau thay đổi này chỉ số vẫn trượt thì báo Tri, không sửa luật đó.

**Test:** prompt mode `synthesize` và `refuse` chứa dòng trên dù lượt có hay không có
tag `exercise_protocol`.

## Task 6 — Bộ đo

**File:** `local_tests/run_context_probe.py`.

- `dose_not_in_evidence` tính và báo cáo cho **mọi nhóm**.
- Thêm cột `retry_fixed`: lượt có retry mà `grader_result` cuối là `pass`.
- Thêm cột `unverified_line`: câu trả lời cuối có dòng "has not been verified" /
  "chưa được kiểm chứng".
- Bảng tổng hợp tách theo ngôn ngữ: số lượt có tag, số retry, số `pass_with_warning`,
  số `unverified_line`, thời gian trung bình.

---

## Kiểm chứng

**Test tự động:** backend và frontend như vòng 3. Fail duy nhất được phép là
`test_phase6_circuit_breaker.py::test_planner_degrades_when_no_provider_can_be_built`.

**Đo → `docs/tracking/context-probe-V8.md`** (persona Anne, graph thật, 51 lượt):

| Chỉ số | Ngưỡng | V7 |
| --- | --- | --- |
| Bảng chọn tool và bảng Kimodo | như V7 | — |
| `dose_not_in_evidence`, mọi nhóm | 0 | 4 (nhóm c) |
| Lượt tiếng Anh có tag bị retry | ≤ 2/15 | 6/15 |
| Lượt retry mà vẫn `pass_with_warning` | 0 | 7/7 |
| `unverified_line`, tiếng Anh | ≤ 1 | 8 |
| `d5_en`, `d5_vi`: `grader_result`; số lần chạy synthesizer | `pass`; 1 | `pass_with_warning`; 2 |
| `d1`–`d3`: có dẫn nguồn | 6/6 | 5/6 |
| `safety_line_repeated` | 0 | 0 |
| Nhóm (e): cảnh báo an toàn có mặt | 2/2 | 2/2 |
| Thời gian trung bình lượt có tool | không tăng so với V7 | 10,5 s (không retry), 20,2 s (retry) |
| Giọng Anne (regex) | không giảm so với V7 | 19/26 |

**Thử tay trên UI local** (`VVA_PERSONA_SOURCE=files`), ngoài 10 bước của kế hoạch
vòng 2:

11. Một câu hỏi bài tập bằng tiếng Anh: bong bóng chat không bị nối hai bản nháp.
12. "mình bị đau ngực khi tập": câu cảnh báo hiển thị ngay trong lượt chat, không
    cần tải lại trang.

Chỉ số nào trượt: ghi số đo và nguyên văn vào worklog, báo Tri. Không tự đổi câu
chữ prompt hay regex để đo lại.

## Báo cáo khi xong

Một mục trong worklog của ngày: commit theo task; bảng V7 → V8; tên từng test fail;
bảng dương tính và âm tính của Task 3 và Task 4; mọi lệnh dừng đã kích hoạt; mọi
chỗ làm khác kế hoạch kèm lý do.

Không merge, không sync persona, không ingest, không đẩy `release` khi chưa được
Tri yêu cầu.
