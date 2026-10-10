---
tags: [plan, agent, grader]
status: done
date: 2026-10-05
---

# Grader kiểm theo node — Implementation Plan

> **Dành cho agent thực thi.** Làm lần lượt từng task, từng bước, theo đúng thứ tự.
> Không thiết kế lại, không gộp task, không làm việc ngoài danh sách. Mỗi bước có ô
> `- [ ]` để đánh dấu. Gặp điều kế hoạch không lường hoặc một bước không làm được
> như mô tả: dừng, ghi vào worklog, báo Tri.

**Mục tiêu:** Câu trả lời trên màn hình, trong DB và trong giọng nói là một văn bản
duy nhất; retry không còn viết lại cả câu trả lời; grader không còn dùng regex để
quyết định.

**Kiến trúc:** Hai chỗ kiểm, mỗi chỗ quay lại đúng node có lỗi, tối đa một lần.
Kiểm 1 chạy sau `tools` (không tốn LLM): không gọi tool hoặc tool lỗi thì quay lại
`retriever_agent`. Kiểm 2 chạy trong `grader` (một lời gọi LLM): evidence có mà câu
trả lời thiếu thì quay lại `synthesizer` để viết thêm phần thiếu. Câu an toàn và
dòng nguồn do code phát thẳng vào luồng stream.

**Tech stack:** Python 3, LangGraph 1.1.6, langgraph-prebuilt 1.0.9, LangChain,
pydantic, pytest. Không thêm thư viện.

```
planner → retriever_agent → tools → [kimodo] → synthesizer → grader → END
              ↑                │                    ↑           │
              └── kiểm 1 ──────┘                    └── kiểm 2 ─┘
```

## Ràng buộc chung

- Nhánh: `feature/grader-contract`, tạo từ `origin/feature/langgraph-rewrite`.
  Không làm trên `release`. Không `git push origin HEAD:release`. Không force-push.
- Mỗi task một commit; sau mỗi commit chạy `git push origin feature/grader-contract`.
- Không ghi vào Neon (không migration, không ingest, không sync persona).
- Không sửa `agenticRAG/langgraph_agents/personas/**`.
- Không add, commit hay stash `.claude/CLAUDE.md` (file này đang có sửa đổi cục bộ).
- Không in ra DSN, mật khẩu, API key hay nội dung `agenticRAG/.env`.
- Không đổi: planner, danh sách 8 tag, `_derive_mode`, frontend, schema DB.
- Bất biến: **chuỗi token đã stream bằng đúng `final_answer`**. Mọi thứ thêm vào câu
  trả lời đều nối ở cuối; không gì đã stream bị xóa hay viết lại.
- Câu chữ prompt trong kế hoạch này chép nguyên văn. Không tự sửa câu chữ.
- Ghi việc đã làm vào `docs/worklogs/DD-MM-YYYY.md` (ngày thực thi); không sửa mục
  của người khác.

| Việc | Lệnh (PowerShell, chạy ở gốc repo) |
| --- | --- |
| Python | `C:\Miniconda\envs\firstconda\python.exe` (không dùng `python` trần) |
| Toàn bộ test backend | `& C:\Miniconda\envs\firstconda\python.exe -m pytest tests/langgraph_agents -m "not integration and not e2e" -W ignore::PendingDeprecationWarning -q` |
| Một file test | `& C:\Miniconda\envs\firstconda\python.exe -m pytest <đường dẫn> -W ignore::PendingDeprecationWarning -q` |
| Test frontend | trong `ECA_UI/frontend`: `npm run build`; `npx vitest run` |
| Bộ đo | `& C:\Miniconda\envs\firstconda\python.exe agenticRAG/langgraph_agents/local_tests/run_context_probe.py --label <tên>` |

Fail duy nhất được phép trong toàn bộ test backend:
`test_phase6_circuit_breaker.py::test_planner_degrades_when_no_provider_can_be_built`.

Viết tắt đường dẫn: `LA/` = `agenticRAG/langgraph_agents/`, `T/` = `tests/langgraph_agents/`.

## Bản đồ file

| File | Việc |
| --- | --- |
| `LA/evidence.py` (mới) | Danh sách mục evidence của lượt; phân loại kết quả tool |
| `LA/tag_contract.py` (mới) | Bảng 8 tag; câu mở đầu, dòng kết, dòng nguồn; câu mẫu an toàn |
| `LA/sources.py` | Thêm bảng nguồn dẫn được |
| `LA/routing.py` | Hàm `retrieval_fault`; routing sau retriever, sau tools, sau grader |
| `LA/graph.py` | `ToolNode` bắt lỗi tool; bảng cạnh mới |
| `LA/nodes/retriever_agent.py` | Khối "Second attempt"; bỏ đọc `grader_feedback` |
| `LA/nodes/synthesizer.py` | Phát câu mở đầu; prompt mới; chế độ viết thêm |
| `LA/nodes/grader.py` | LLM chấm; phát dòng kết; bỏ chèn câu và câu "chưa được kiểm chứng" |
| `LA/llm.py` | Thêm role `grader` |
| `LA/state.py` | Thêm `grader_detail` |
| `LA/api/main.py` | Log `stream_final_mismatch`; `grader_detail` vào `meta` |
| `LA/local_tests/run_context_probe.py` | Cột đo mới |
| `scripts/eval_grader_judge.py` (mới) | Đo LLM chấm trên bộ nhãn |

## Năm tình huống dễ gãy (mỗi cái đã có test trong task ghi bên cạnh)

1. LLM chính lỗi ngay byte đầu ở lượt đã phát câu mở đầu → fallback vẫn phải chạy và
   `final_answer` vẫn bắt đầu bằng câu mở đầu. (Task 4)
2. LLM chấm trả mục lạ, thiếu mục, hoặc JSON hỏng → không retry, không ném lỗi. (Task 5)
3. Tool lỗi cả hai vòng → lượt vẫn ra câu trả lời, `retriever_agent` chạy đúng 2 lần. (Task 2)
4. LLM lỗi ở lần viết thêm → `final_answer` giữ nguyên bản đã hiện, dòng kết vẫn được
   nối, không có chuỗi lỗi nào chen vào. (Task 6)
5. Locale ngoài `vi`/`en` → câu an toàn và dòng nguồn rơi về tiếng Anh, không rỗng. (Task 3)

## Task 0 — Nhánh và số đo gốc

Nhánh `feature/grader-contract` từ `origin/feature/langgraph-rewrite`; toàn bộ
test backend; bộ đo `--label V8-baseline`; commit baseline probe.

## Task 1 — Evidence dùng chung

Tạo `LA/evidence.py` (`EvidenceItem`, `classify_tool_result`, `evidence_messages`,
`evidence_items`, `render_evidence`, `named_items`); `synthesizer._extract_tool_results`
còn một dòng; test `T/test_evidence_items.py`.

## Task 2 — Kiểm 1: sau retriever

`routing.retrieval_fault` / `failed_tool_names` / `route_after_tools`; `ToolNode`
bắt lỗi tool thành `ToolMessage {"error": ...}`; `retriever_agent` khối
"Second attempt"; test `T/test_retrieval_check.py`.

## Task 3 — Bảng tag, câu cố định, dòng nguồn

`LA/tag_contract.py` (`TAG_CONTRACT`, `CheckItem`, `model_tags`, `check_items`,
`opening_line`, `closing_lines`, `closing_items_note`, `source_line`,
`get_safety_text`); `sources.py` thêm `CITATIONS`; test `T/test_tag_contract.py`.

## Task 4 — Code phát câu an toàn và dòng nguồn

Synthesizer phát câu mở đầu + contract note; grader `_finish` nối dòng kết;
bỏ câu "chưa được kiểm chứng"; test `T/test_code_written_lines.py`.

## Task 5 — Kiểm 2: LLM chấm

`_judge` (checklist → `synth_missed` / `source_silent` / `ok` / `no_evidence`);
`grader_detail` trong state; test `T/test_grader_judge.py`.

## Task 6 — Viết thêm phần thiếu

`route_after_grader` → `synthesizer`; khối `## Add what is missing`; chỉ nối
phần thiếu; test `T/test_addition_pass.py`.

## Task 7 — API: kiểm bất biến

`streamed_text` so với `final_answer` → log `stream_final_mismatch`;
`grader_detail` vào `meta`.

## Task 8 — Đo

Fixture thêm bản ja/fr; `scripts/eval_grader_judge.py`; cột probe V8 mới;
ngưỡng ở mục Kiểm chứng.

## Task 9 — Tài liệu

Flow, plan, tech-debt, worklog (task hiện tại).

---

## Kiểm chứng

**LLM chấm trên bộ nhãn** (`docs/tracking/grader-judge-eval.md`)

| Chỉ số | Ngưỡng |
| --- | --- |
| Sai so với nhãn, câu tiếng Việt và tiếng Anh, 4 tag do model viết | ≤ 5/69 |
| Sai so với nhãn, 4 câu tiếng Nhật và tiếng Pháp | ≤ 2/16 |
| Lời gọi hỏng | 0 |

**Bộ đo V8** (`docs/tracking/context-probe-V8.md`, persona Anne, 51 lượt)

| Chỉ số | Ngưỡng |
| --- | --- |
| `stream_equals_final` | 51/51 |
| `fixed_line_count`: mỗi câu an toàn của lượt | đúng 1, mọi lượt |
| Lượt có `evidence_citation` và câu trả lời nêu một mục thư viện hoặc chữ "NHS": `source_line` không rỗng | tất cả |
| Câu trả lời nói thư viện không có và không nêu mục nào: dòng nguồn có nhãn thư viện | 0 |
| Chuỗi "has not been verified" / "chưa được kiểm chứng" | 0 |
| `retriever_runs == 2` | ≤ 2/51 |
| `retry` | ≤ 3/31 lượt có tag |
| `d5_en`, `d5_vi` | không `retry` |
| `addition_repeats_draft` | 0 |
| Thời gian trung bình lượt có tag | không cao hơn V8-baseline |
| `dose_not_in_evidence`, mọi nhóm | 0 |
| Bảng chọn tool và bảng Kimodo | như V8-baseline |
| Giọng Anne (regex) | không giảm so với V8-baseline |

Chỉ báo cáo, không đặt ngưỡng: `model_wrote_own_safety`, phân bố `grader_detail`,
thời gian trung bình một lời gọi LLM chấm.

**Thử tay trên UI local** (backend chạy với `VVA_PERSONA_SOURCE=files`) — do Tri làm.

## Ghi chú thực thi (04–05/10/2026, khác kế hoạch)

- T8b (ngoài kế hoạch): tắt suy nghĩ ngầm cho role `grader`
  (`extra_body {"thinking": {"type": "disabled"}}`, timeout 5s, max_retries 0) —
  lời chấm từ ~1,8s TB xuống 781ms. Planner/retriever/synthesizer giữ nguyên.
- Vi+en 4/69, ja+fr 1/16, 0 gọi hỏng.
- Ngưỡng thời gian vi "trượt 0,4s, Tri chấp nhận" (04/10): trung vị chậm hơn
  ~0,8s (vi) và ~1,0s (en) trên các lượt baseline không retry — giá cố định của
  LLM chấm, không phải nhiễu. Không chạy lại V8.
- T6: planner tự thêm `referral_advice` khi có `red_flag_screen` (D33) nên kỳ vọng
  `final_answer` trong test gồm cả dòng referral.
