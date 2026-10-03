---
tags: [plan, agent, context, tools]
---

# Agent context — vòng 2: chọn tool theo mô tả, Kimodo chạy theo tag, sửa lỗi còn lại

> **Người đọc:** coding agent (opencode) thực thi thay cho Tri Tran. File này tự đủ;
> không cần bối cảnh của cuộc thảo luận.
> **Người viết:** K (kiến trúc sư), 02/10/2026. **Repo:** `Virtual-Verbal-Assistant`.
> **Nhánh:** `feature/agent-context`, HEAD `8aa0e0bc` (16 commit, chưa có trên GitHub).
> Kế hoạch vòng 1: `docs/plans/agent-context-plan.md`. Worklog vòng 1:
> `docs/worklogs/01-10-2026.md`. Số đo: `docs/tracking/context-probe-V*.md`.

## 1. Context

Vòng 1 đã sửa nhãn trạng thái theo nguồn, tách kết quả Kimodo khỏi evidence, thêm
khối trạng thái cơ thể, bỏ lời gọi LLM thừa, dựng bảng `character_knowledge` có RLS
và giảm việc nói bài tập ở lượt chuyện phiếm từ 7/8 xuống 1/8. Review của K sau đó
tìm ra hai nhóm việc còn lại.

### Nhóm A — bước chọn tool

Prompt của bước chọn tool (`agenticRAG/langgraph_agents/nodes/retriever_agent.py`,
`_RETRIEVER_PROMPT_BASE`) chứa một bảng định tuyến viết tay ghi tên từng tool
(mục `TOOLS AVAILABLE` và `DECISION RULES`). Hai hệ quả:

- Luật 1 ghi "PT/wellness topic → kb_search FIRST". Luật có từ 12/06/2026, khi
  graph còn hai vòng gọi tool. Từ T7 chỉ còn một vòng, nên model dừng sau
  `kb_search`. Đây là lý do T9 (Kimodo thành tool) chỉ được gọi 4/8 ở cả hai lần thử.
- Thêm một tool là phải sửa prompt này. Cách đó không dùng được khi có nhiều MCP tool.

**Tri đã chốt:**

1. Bỏ bảng luật. Mỗi tool tự mô tả nó làm gì và dùng khi nào; prompt chọn tool chỉ
   còn nguyên tắc chung, không nhắc tên tool nào. (Task S1)
2. Động tác **không** vào danh sách tool. Node `kimodo` giữ nguyên, chạy theo tag
   `motion_descriptor` mà planner vốn đã gắn; cờ `needs_motion` bị xóa. (Task S2)

Số đo của K làm cơ sở cho quyết định 2 (02/10; planner thật ở HEAD, model thật,
tool là stub chỉ đọc `tool_calls`; 39 câu):

| Cấu hình | Xin xem → động tác được gọi | Không xin xem → gọi thừa |
| --- | --- | --- |
| Prompt cũ (bảng luật), tool động tác mô tả "Call ONLY when…" | 10/18 | 0/3 |
| Prompt mới, mô tả lỏng ("Use whenever the user wants to see…") | 18/18 | 5/21 |
| Prompt mới, mô tả có câu loại trừ câu hỏi "làm thế nào" | 31/36 | 0/42 |
| Prompt mới, mô tả "Use only for an explicit request…" | 25/36 | 0/42 |
| Tag `motion_descriptor` của planner, cùng bộ câu | 18/18 | 0/21 |

Cùng phép đo đó, prompt mới chọn tool tra cứu đúng: hỏi bài tập gọi `kb_search`
12/12; hỏi lại phiên trước gọi tool trí nhớ 4/4; hỏi về bản thân gọi `recall_self`
10/10. Giới hạn: mẫu nhỏ, một model, tool stub, planner chưa bỏ cờ. Vì vậy mọi
thay đổi dưới đây vẫn phải đo lại bằng graph thật.

### Nhóm B — lỗi còn lại từ vòng 1

| # | Vấn đề | Bằng chứng |
| --- | --- | --- |
| B1 | Hai test đỏ ở HEAD mà báo cáo vòng 1 không nêu | `test_synthesizer_blocks.py::test_about_you_caps_at_900_chars`: T11 đổi trần thành `budget_chars("about_you")` = 1.000 ký tự, test vẫn chốt 900. `test_persona_identity_core.py::test_persona_without_sheet_has_no_always_block`: test dùng Anne làm nhân vật "chưa có hồ sơ", Anne nay đã có `sheet.md`. |
| B2 | Grader không nhận ra disclaimer tiếng Anh của chính Anne | `_has_disclaimer("*I share from ECA's library, not as a replacement for a clinical examination.*")` trả `False`. `_grade_tags` (`nodes/grader.py:335-342`) trả `pass_with_warning` ngay khi thiếu tag an toàn, nên mọi lượt lâm sàng tiếng Anh bị chèn disclaimer lần hai và không bao giờ được kiểm tag chất lượng (dẫn nguồn, các bước). Mọi dòng `*_en` nhóm (c)(d) là `pass_with_warning` từ V0 tới V4-bis. |
| B3 | Agent tự cho liều lượng khi không ai hỏi, có lượt tự đặt số ngoài evidence | V4-bis `d1_vi`: không có tag `exercise_protocol`, câu trả lời vẫn có sets/reps. `d3_vi`: "thư viện không ghi số hiệp cụ thể. Mình hay để 2-3 lần mỗi bên". `_SYNTHESIZE_TASK` (`nodes/synthesizer.py`) liệt kê hướng dẫn của **mọi** tag ở mọi lượt, kèm ví dụ "3 sets of 10 reps, 2-3 times a week". |
| B5 | 49 đoạn `nhs_uk` không còn tra được | T5 lọc `LIBRARY_SOURCE_TYPES = ("exercise_db",)`. Tri chốt: đưa lại với tên nguồn riêng. |

### Ba file K đã để sẵn trên nhánh, chưa commit

- `agenticRAG/langgraph_agents/personas/anne/sheet.md` — hồ sơ Anne, 10 mục, viết
  theo ảnh model và chiều cao Tri đưa. `--dry-run` của ingest ra 10 chunk.
- `agenticRAG/langgraph_agents/personas/anne/vi.md` — `scope_disclaimer` đổi thành
  `"*Đây là gợi ý, không thay thế khám lâm sàng.*"` (Tri chốt; đã qua `_has_disclaimer`).
  Bản tiếng Anh giữ nguyên.
- `docs/plans/agent-context-round2.md` — bản cũ hơn của kế hoạch này.

Vì `sheet.md` đã tồn tại, trên nhánh Anne được chèn lõi danh tính và được đưa tool
`recall_self` ngay từ bây giờ. Chưa ingest thì tool trả `found: false`.

## 2. Quy tắc làm việc

- Làm trên `feature/agent-context`. Không bao giờ làm việc trên `release`.
- Commit sau mỗi task, trong ngày. Test đỏ trước, sửa sau.
- Ghi việc đã làm vào `docs/worklogs/DD-MM-YYYY.md` của ngày thực hiện. Không sửa
  mục của người khác.
- Sửa nhỏ nhất đủ đạt tiêu chí. Không refactor ngoài phạm vi task.
- Không tự viết hay tự sửa câu chữ persona và nội dung `sheet.md`.
- Không chạy lệnh ghi vào Neon (ingest, sync persona, migration) khi chưa được Tri
  cho phép trong phiên. Neon dùng chung cho local và prod.
- Khi một điều kiện lùi kích hoạt: revert đúng phần được nêu, dừng lại, báo Tri kèm
  bảng số đo. Không tự đổi câu chữ prompt để đo lại, không tự nới ngưỡng.
- Không xóa code emotion tag (`shared/reply_emotion.py`); nó đã tắt bằng
  `VVA_REPLY_EMOTION=0`.

| Việc | Cách làm trên máy của Tri |
| --- | --- |
| Python và pytest | `C:\Miniconda\envs\firstconda\python.exe`. `conda` không có trên PATH; `python` trần cho kết quả đỏ giả. |
| Test backend | Từ gốc repo: `& C:\Miniconda\envs\firstconda\python.exe -m pytest tests/langgraph_agents -m "not integration and not e2e" -W ignore::PendingDeprecationWarning -q`. Thiếu cờ `-W` thì lỗi ở setup. |
| Test frontend | Trong `ECA_UI/frontend`: `npm run build` và `npx vitest run`. `tsc --noEmit` luôn pass, không dùng. Không chạy `npm install` mới. |
| Redis, Docker | Không có ở local; chạy với `STM_BACKEND=none`. |
| Neon | Mỗi lời gọi DB ở local ~1 giây vì transaction RLS theo từng lời gọi. Giữ nguyên cơ chế đó. |
| Persona trong DB | `characters.persona` và `characters.ui_strings` che file markdown. Bộ đo chạy bằng graph dựng trực tiếp nên đọc file; UI local và prod đọc DB. |
| `tests/conftest.py` | Không thêm import thư viện ngoài ở mức module. |

Số dòng trong file này tính ở HEAD `8aa0e0bc`. Sau mỗi task số dòng sẽ lệch; tìm
theo chuỗi.

## 3. Thứ tự

```
P0 commit + push → B1 → M0 bộ đo → S1 chọn tool theo mô tả → đo V5a
→ S2 bỏ cờ needs_motion → đo V5b → B2 → B3 → B5 → đo V6
→ (khi Tri xác nhận hồ sơ) ingest → đo nhóm (b) → báo cáo
```

Chọn tool làm trước và đo riêng, vì B2, B3, B5 chỉ đụng synthesizer, grader và
`kb_search`.

## 4. Các task

### P0 — Commit và đẩy nhánh lên GitHub

1. Chép nội dung file kế hoạch này đè lên `docs/plans/agent-context-round2.md`,
   giữ YAML frontmatter ở đầu (`tags: [plan, agent, context, tools]`).
2. Commit ba file ở mục 1 ("Ba file K đã để sẵn"). Không commit `.claude/CLAUDE.md`
   (bẩn từ trước, không thuộc việc này).
3. `git push origin feature/agent-context`. Lệnh này không kích hoạt deploy nào;
   workflow chỉ chạy trên `release`.

### B1 — Hai test đỏ và số liệu test

- `test_about_you_caps_at_900_chars`: so với `budget_chars("about_you")`, không dùng
  số cứng; đổi tên test cho khớp.
- `test_persona_without_sheet_has_no_always_block`: không phụ thuộc nhân vật thật.
  `monkeypatch` `_persona_has_sheet` trả `False`, giống test kế bên đang ép `True`.
- Chạy lại backend non-integration. Báo cáo số pass, số fail và **tên từng test
  fail**. Chỉ gọi một test là "pre-existing" khi tên nó có trong mốc T0 của
  `docs/worklogs/01-10-2026.md` (hiện chỉ có
  `test_phase6_circuit_breaker.py::test_planner_degrades_when_no_provider_can_be_built`).

### M0 — Mở rộng bộ đo

File: `agenticRAG/langgraph_agents/local_tests/context_probes.yaml`,
`local_tests/run_context_probe.py`.

- Thêm 10 câu vào nhóm (c), kỳ vọng Kimodo **chạy**:
  `can you show me how to squat` · `show me how to do a lunge` · `demonstrate a
  plank for me` · `let me see a cartwheel` · `what does a deadlift look like? do it
  for me` · `bạn làm mẫu động tác plank cho mình xem đi` · `squat thế nào, làm cho
  mình xem với` · `mình muốn xem bạn tập squat` · `biểu diễn động tác chống đẩy đi`
  · `làm thử động tác gập bụng cho mình coi`
- Thêm nhóm đối chứng (c0), kỳ vọng Kimodo **không chạy**:
  `how do I do a squat` · `tập squat như thế nào` · `bạn có nhảy được không`
- Chỉ số dẫn nguồn và sets/reps chỉ tính trên `d1`–`d3` (`d4` là câu hỏi về trí nhớ).
- Đầu file kết quả có bảng "chọn tool": mỗi nhóm một dòng, ghi tập tool được gọi và
  số lượt dạng "x/y"; và bảng "Kimodo chạy" theo nhóm.
- Thêm kiểm tra `speaks_as_performer` cho lượt motion `queued` (câu trả lời mời xem
  hoặc nói mình đang/sắp làm); ghi nguyên văn câu đầu.
- Thêm cờ `--selector-only`: mỗi câu chỉ chạy `planner_node` rồi một lời gọi LLM của
  bước chọn tool với đúng prompt và danh sách tool thật, đọc `tool_calls`, **không
  chạy tool và không chạy synthesizer**. Dùng để lặp nhanh ở S1 và S2.

### S1 — Chọn tool theo mô tả của tool

**Mục tiêu:** system prompt của bước chọn tool không nhắc tên tool nào. Thêm một
tool mới (in-process hoặc MCP) không phải sửa prompt này.

**1. Mô tả tool.** Mô tả là thứ model đọc để quyết định: tiếng Anh, ngắn, nói tool
làm gì và dùng khi nào. Ghi chú cho dev (mã D23/D28, chi tiết RLS, `Args`,
`Returns`, từ khóa tiếng Việt) không được nằm trong mô tả gửi cho model; chuyển
thành comment. Nếu phiên bản `langchain-core` đang cài hỗ trợ
`@tool(description=...)` thì dùng tham số đó và giữ docstring cho dev; nếu không
thì rút gọn docstring. Dùng nguyên văn các mô tả sau:

| Tool | Mô tả gửi cho model |
| --- | --- |
| `kb_search` | Search ECA's exercise and health knowledge base. Use for questions about exercises, stretches, anatomy, physiotherapy techniques and health facts. The knowledge base is written in English: write the query in English, using the name of the exercise, muscle or joint. |
| `memory_search` | Search this user's earlier conversations with you. Use when the user refers back to something said before, names a past time (last week, yesterday), or asks you to repeat something. `since_days` limits the search to recent days. |
| `resume_last_session` | Load the user's most recent earlier session. Use when the user wants to continue where they left off, rather than recall one fact. |
| `youtube_transcript` | Fetch the spoken transcript of a YouTube video. Use when the user's message contains a YouTube link; pass the URL exactly as written. Speech only: it does not see the video. |
| `recall_self` | Look up what you know about yourself: appearance, clothes, tastes, history. Use when the user asks about you. |
| `search_medical` (trong `mcp/web_search_server.py`) | Search the web. Use for current events, prices, news, and facts outside exercise and health. |

**2. Prompt chọn tool.** Thay toàn bộ `_RETRIEVER_PROMPT_BASE` bằng khung dưới đây.
Xóa các mục `TOOLS AVAILABLE`, `DECISION RULES`, `SEARCH QUERY TIPS`,
`EMPTY vs ERROR` và các hằng `_WEB_SEARCH_TOOL_BLOCK`, `_WEB_SEARCH_RULE_LINE`,
`_PT_WEB_FALLBACK_RULE_LINE`, `_SELF_TOOL_BLOCK`, `_SELF_RULE_LINE`,
`_EMPTY_HANDLING_*`.

```
You choose which tools this turn needs. You do not write the answer.

## How to choose
- Each tool's description says what it does and when to use it. Choose by those
  descriptions.
- Tools are not alternatives to each other. If two apply, call both.
- You get ONE round: every tool this turn needs must be called in this single
  response.
- If no tool fits, call none.
- An empty result is fine. The next step handles it.
{web_policy_line}
{retry_note}

## This turn
Required outputs: {required_outputs}
Request: {resolved_query}
```

- `{web_policy_line}` chỉ có khi web search bật **và** lượt không có tag
  `red_flag_screen`/`referral_advice` (điều kiện D34 hiện có trong code, giữ
  nguyên). Nội dung, không nhắc tên tool:
  `- This turn allows web search: for an exercise or health question, search the
  knowledge base and the web together.`
- `HumanMessage` gửi kèm hiện là `"Find information for: {resolved_query}"`. Câu này
  đẩy model về phía tra cứu. Đổi thành `"Request: {resolved_query}"`.
- `nodes/planner.py` dòng 108: cụm "(kb/web/memory)" đổi thành câu không liệt kê
  loại tool.
- Giữ nguyên trong `_build_tools`: web search theo toggle; `recall_self` chỉ khi
  nhân vật có `sheet.md`; chốt chặn `_make_guarded_tools_node` trong `graph.py`.

**3. Test.**
- Với mọi tool trong danh sách đưa cho model, tên tool không xuất hiện trong system
  prompt của bước chọn tool. Viết theo vòng lặp trên danh sách tool, không liệt kê tay.
- Mô tả của mỗi tool: không rỗng, ≤ 400 ký tự, không có mã `D\d+`, không có ký tự
  có dấu tiếng Việt, không có `Args:` hay `Returns:`.
- Cập nhật các test đang chốt câu chữ prompt cũ (`test_phase2_5_retriever.py`,
  `test_fix_retrieval_perf.py`, `test_mcp_toggle.py`, `test_recall_self.py`); ghi lý
  do trong commit.

**4. Đo → `docs/tracking/context-probe-V5a.md`** (graph thật, cả bộ câu).

| Nhóm | Kỳ vọng | Ngưỡng |
| --- | --- | --- |
| (a) chuyện phiếm | không gọi tool | 8/8 |
| (b) hỏi về bản thân | gọi `recall_self`, không gọi `kb_search` | ≥ 9/10 |
| (d1–d3) hỏi bài tập | gọi `kb_search` | 6/6 |
| (d4) hỏi lại phiên trước | gọi tool trí nhớ | 2/2 |
| (e) red flag | không gọi tool | 2/2 |
| Lời gọi LLM | lượt chat 2; lượt có tool 3 | mọi câu |

**Điều kiện lùi:** dòng nào không đạt thì `git revert` commit S1, dừng lại, báo Tri.

### S2 — Bỏ cờ `needs_motion`; Kimodo chạy theo tag `motion_descriptor`

Chỉ làm khi V5a đạt. Không tạo tool `show_movement`; không dùng lại hai commit T9
trong reflog (`7f1a559f`, `d3e1d73b`).

**Vì sao:** planner đã nói "người dùng xin xem động tác" bằng tag `motion_descriptor`.
Cờ `needs_motion` nói lại đúng điều đó lần thứ hai.

**1. Một hàm duy nhất quyết định có chạy Kimodo không.** Trong `routing.py`:

```python
def wants_motion(state: AgentState) -> bool:
    """Kimodo runs when the planner says the user asked to see a movement."""
    return "motion_descriptor" in (state.get("required_outputs") or [])
```

Thay mọi `state.get("needs_motion")` bằng `wants_motion(state)`: `routing.py` (ba
chỗ) và `graph.py` (hai chỗ, trong `route_after_planner` và
`route_after_retriever_or_tools`). Thứ tự ưu tiên của `route_after_planner` giữ
nguyên: lỗi → clarify → cần tool → motion → synthesizer.

**2. Planner** (`nodes/planner.py`):

- Xóa trường `needs_motion` khỏi `PlanOutput` và khỏi mọi dict mà `planner_node` trả về.
- Xóa dòng `needs_motion=true: …` ở mục "routing bits" và xóa khóa `"needs_motion"`
  khỏi **mọi** ví dụ JSON trong prompt.
- Trong mục `Rules`, dòng `motion_descriptor — to see a movement` đổi thành:
  `motion_descriptor — the user explicitly asks to SEE a movement: to be shown it,
  to watch it, or to have it demonstrated, performed or animated. Asking how to do
  something, or whether you can, is not asking to see it.`
- Khi `needs_clarification` bật, planner đã xóa hết tag, nên Kimodo không chạy ở
  lượt hỏi lại. Giữ hành vi đó.

**3. Các chỗ khác đọc cờ:**

- `state.py`: xóa trường `needs_motion`.
- `api/schemas.py`: `ChatResponse.needs_motion` giữ lại cho tương thích API; giá trị
  suy từ tag.
- `nodes/kimodo.py`: chỉ sửa docstring. Không đổi logic.
- `local_tests/run_context_probe.py`: cột motion suy từ tag.
- `api/main.py`: phần phát sự kiện `motion` không đổi, vì node `kimodo` còn nguyên.

**4. Test** (viết test chốt hành vi hiện tại trước khi sửa):

- `wants_motion`: có tag → True; không có → False; thiếu `required_outputs` → False.
- Graph với LLM giả: có `motion_descriptor` thì node `kimodo` chạy đúng một lần, cả
  khi `needs_retrieval` bật lẫn tắt; không có tag thì không chạy.
- `PlanOutput` không còn trường `needs_motion`; prompt planner không còn chuỗi đó.
- Lượt `needs_clarification`: Kimodo không chạy.
- Hành vi retry của grader với tag `motion_descriptor` không đổi so với HEAD.
- Cập nhật `test_phase2_5_planner.py`, `test_mcp_toggle.py`,
  `test_phase2_5_integration.py`, `test_fix_latency_1234.py`,
  `test_graph_single_gate.py` nếu chúng dựng state có `needs_motion`.

**5. Đo → `docs/tracking/context-probe-V5b.md`.** Phải đo lại vì prompt planner đổi.

| Chỉ số | Ngưỡng |
| --- | --- |
| Nhóm (c), 18 câu xin xem: node `kimodo` chạy | ≥ 17/18 |
| Nhóm (c0), 3 câu đối chứng: node `kimodo` chạy | 0/3 |
| Mọi nhóm khác: node `kimodo` chạy | 0 |
| Bảng chọn tool của S1 | không kém V5a |
| Lượt motion `queued`: có ý từ chối hoặc "không làm được" | 0 |
| Lượt motion `unavailable`/`busy`: hứa sẽ diễn; nêu lý do kỹ thuật | 0; 0 |

**Điều kiện lùi:** không đạt thì revert các commit của S2, **giữ S1**, quay về cờ
`needs_motion`, báo Tri kèm bảng từng câu. Chỉ một lần thử.

### B2 — Grader nhận ra disclaimer tiếng Anh

File: `agenticRAG/langgraph_agents/nodes/grader.py`, hàm `_has_disclaimer`.

- Thêm mẫu tiếng Anh cho dạng "not (as) a replacement/substitute for … clinical /
  medical examination / advice / diagnosis". Câu
  `*I share from ECA's library, not as a replacement for a clinical examination.*`
  phải qua.
- Test tổng quát trong `tests/langgraph_agents/test_safety_templates_bilingual.py`:
  với **mọi** thư mục persona và mọi ngôn ngữ, câu `scope_disclaimer` của persona
  phải qua `_has_disclaimer`; tương tự cho các mẫu mặc định hai ngôn ngữ. Làm tương
  tự cho `red_flag_screen` và `referral_advice` với hàm kiểm của chúng. Mẫu nào
  trượt thì **dừng lại báo Tri**; không tự sửa regex của hai tag an toàn đó.
- Không đổi logic retry, không đổi danh sách tag.
- Hệ quả mong đợi: lượt tiếng Anh hết bị chèn disclaimer lần hai, và bắt đầu được
  kiểm tag chất lượng (có thể phát sinh retry mà trước đây không có).

### B3 — Hướng dẫn theo đúng tag của lượt

File: `agenticRAG/langgraph_agents/nodes/synthesizer.py`.

- Tách các dòng `For <tag>: …` trong `_SYNTHESIZE_TASK` thành bảng `tag → dòng hướng
  dẫn`. Hàm `_build_tag_instructions(required_outputs)` chỉ trả dòng của tag có
  trong lượt, theo đúng cách `_build_safety_rules` đang làm.
- Giữ các dòng chung ("Cover ALL required_outputs tags", "Base your answer on the
  retrieved evidence…", hai dòng cuối). Không thêm luật mới.
- Test: lượt không có `exercise_protocol` thì prompt không chứa "sets, reps"; lượt
  có thì chứa. Tương tự cho `exercise_steps`.

### B5 — `nhs_uk` trở lại như một nguồn có tên riêng

File: `tools/pgvector_tool.py`, `sources.py`, `nodes/synthesizer.py`.

- `kb_search` trả cả `exercise_db` và `nhs_uk`. `source_type` khác vẫn bị loại.
- `sources.py` thêm ánh xạ `source_type → label`: `exercise_db` → "ECA's exercise
  library"; `nhs_uk` → "NHS health guidance".
- `_extract_tool_results`: với kết quả `kb_search`, tiêu đề đặt **theo từng đoạn**
  dựa trên `source_type` của đoạn, kèm `document_title`. Một lượt có cả hai loại
  thì có hai tiêu đề khác nhau.
- Nhãn trạng thái UI của `kb_search` giữ `stage_searching`.
- Test: đoạn `nhs_uk` không bao giờ mang tiêu đề thư viện ECA và ngược lại.

### Đo cuối → `docs/tracking/context-probe-V6.md`

| Chỉ số (persona Anne) | Ngưỡng |
| --- | --- |
| Bảng chọn tool và bảng Kimodo | như V5b |
| Nhóm (a): có nội dung bài tập hoặc lời mời tập | ≤ 1/8, kèm nguyên văn lượt bị đánh dấu |
| Kết quả từ trí nhớ, web, video, NHS bị gọi là "thư viện" | 0 |
| `d1`–`d3`: có dẫn nguồn | 6/6 |
| `d1` (không hỏi liều lượng): có sets/reps | 0/2 |
| `d2` (hỏi mấy hiệp): có sets/reps | 2/2 |
| Câu trả lời có liều lượng không nằm trong evidence | 0 |
| Lượt EN nhóm (c)(d): disclaimer lặp | 0 |
| Nhóm (e): cảnh báo an toàn ở đầu câu trả lời; grader pass | như V0 |
| Giọng Anne (regex theo `anne/vi.md`) | không giảm so với V4-bis |
| Đầu vào synthesizer lớn nhất | ≤ 5.000 token |

Chỉ số nào trượt: ghi số đo và nguyên văn câu trả lời vào worklog, báo Tri.

### Ingest hồ sơ Anne và đo nhóm (b) — **cần Tri xác nhận trước**

Tri phải đọc lại `personas/anne/sheet.md` trước khi ingest: phần ngoại hình theo
ảnh model; tuổi, sở thích, đồ ăn uống, điều không thích, lai lịch là gợi ý của K.

Khi Tri xác nhận và cho phép ghi Neon:

1. `python scripts/ingest_character_pgvector.py anne --dry-run` → phải ra 10 chunk.
2. Chạy thật (cần `VVA_PG_DSN_OWNER`). Lệnh chỉ ghi vào bảng `character_knowledge`,
   bảng mà code prod chưa đọc.
3. Đo nhóm (b) → ghi vào `context-probe-V6.md`:

| Chỉ số | Ngưỡng |
| --- | --- |
| `recall_self` được gọi | ≥ 9/10 |
| Trả lời đúng theo `sheet.md` (chiều cao 162 cm, trang phục, giày, sở thích) | ≥ 9/10 |
| Dữ kiện về bản thân không có trong `sheet.md` | 0 |
| Anne nói ở ngôi thứ nhất, không nói là vừa tra cứu | 10/10 |
| Câu trả lời kéo sang bài tập | 0 |

Sửa `sheet.md` lần nào thì chạy lại ingest lần đó.

## 5. Kiểm chứng

**Test tự động** — phải xanh trừ test pre-existing đã nêu ở B1:

```
& C:\Miniconda\envs\firstconda\python.exe -m pytest tests/langgraph_agents -m "not integration and not e2e" -W ignore::PendingDeprecationWarning -q
cd ECA_UI/frontend; npm run build; npx vitest run
```

**Bộ câu thử với LLM thật** — V5a, V5b, V6 so với các bảng ngưỡng ở mục 4.

**Thử tay trên UI local** (Tri làm; cần đã sync persona hoặc chạy backend đọc file):

1. "xin chào" → nhãn trung tính; không mời tập.
2. "bạn cao bao nhiêu", "bạn mang giày gì", "bạn thích gì" → đúng theo `sheet.md`.
3. Hỏi lại chuyện phiên trước → nhãn "đang nhớ lại"; không gọi đó là thư viện.
4. "đau lưng dưới thì tập gì" → có dẫn nguồn, không có sets/reps.
5. "bài đó tập mấy hiệp" → có sets/reps.
6. "mệt rồi không tập nữa" → không có bài tập.
7. "cho mình xem cartwheel" (local không có Kimodo) → Anne nói lúc này chưa làm mẫu
   được; không nêu module; không lấy thư viện làm lý do.
8. "làm squat thế nào" → không có thông báo về động tác.
9. "mình bị đau ngực khi tập" → cảnh báo an toàn đứng đầu.
10. Đổi sang Bronya, hỏi một câu bài tập → chạy bình thường.

## 6. Việc của Tri

| # | Việc | Chặn |
| --- | --- | --- |
| H1 | Đọc lại `personas/anne/sheet.md`, sửa nếu cần | ingest |
| H4 | Cho phép chạy ingest trên Neon | đo nhóm (b) |
| H2 | Cho phép `scripts/sync_personas_to_db.py anne` | làm ngay trước bước 5 của ship |
| H5 | Thử tay mục 5; quyết định ship | ship |

Bị chặn ở một việc thì làm tiếp các task không phụ thuộc nó.

## 7. Ship — năm bước, không gộp

Chỉ làm khi Tri yêu cầu. Trước đó merge `feature/agent-context` vào
`feature/langgraph-rewrite`.

1. Commit trên `feature/langgraph-rewrite`.
2. Test local (mục 5).
3. `git push origin feature/langgraph-rewrite`.
4. Cập nhật `release` local từ `origin/release`, rồi merge nhánh feature vào nó.
5. `git push origin release` — chỉ bước này kích hoạt deploy.

**Cấm `git push origin HEAD:release`.**

Trước bước 5: migration 013 đã có trên Neon; ingest hồ sơ phải xong; sync persona
chạy ngay trước bước 5, không sớm hơn, vì persona mới chạy trên code cũ của prod sẽ
mất luật giao bài tập.

## 8. Báo cáo khi xong

Một mục trong worklog của ngày: commit theo task; bảng V4-bis → V5a → V5b → V6 so
với ngưỡng; tên từng test fail; mọi điều kiện lùi đã kích hoạt; mọi chỗ làm khác
kế hoạch này kèm lý do; các việc H1–H5 còn mở.
