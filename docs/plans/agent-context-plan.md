---
title: "Agent context — một cổng tool, nguồn có kiểu, kiến thức về bản thân"
author: K (Senior Solution Architect)
date: 2026-10-01
branch: feature/agent-context
base: feature/langgraph-rewrite
tags: [plan, agent, context]
---

# Implementation plan — Agent context: một cổng tool, nguồn có kiểu, kiến thức về bản thân
> **Người đọc:** coding agent (opencode) thực thi thay cho Tri Tran. Bạn chưa có bối
> cảnh nào của cuộc thảo luận; mọi thứ cần biết nằm trong file này.
> **Người viết:** K (kiến trúc sư). **Ngày:** 01/10/2026. **Repo:** `Virtual-Verbal-Assistant`.
> **Nhánh gốc:** `feature/langgraph-rewrite`.

---

## 1. Bối cảnh

ECA là trợ lý sức khỏe có avatar 3D. Backend là một LangGraph trong
`agenticRAG/langgraph_agents/`, chạy trên Lambda `vva-agent`. Luồng một lượt chat:

```
memory → planner → [retriever_agent ⇄ tools] → [kimodo] → synthesizer → [grader]
```

- `planner` (1 lời gọi LLM): gắn tag, viết lại câu hỏi, bật cờ `needs_retrieval`, `needs_motion`.
- `retriever_agent` (LLM chọn tool) + `tools`: `kb_search`, `memory_search`, `resume_last_session`, `youtube_transcript`, và web search qua MCP (MCP tắt trên Lambda).
- `kimodo`: xếp hàng một job sinh động tác 3D. Chạy trên đường riêng theo cờ `needs_motion`.
- `synthesizer` (1 lời gọi LLM): suy ra mode (`chat` / `synthesize` / `refuse` / `clarify`), ghép persona + task + evidence, viết câu trả lời.
- `grader`: kiểm tag bằng regex; thiếu tag an toàn thì chèn mẫu, thiếu tag chất lượng thì retry.

### Bốn lỗi phải sửa

| # | Lỗi người dùng thấy | Nguyên nhân trong code |
| --- | --- | --- |
| P1 | Mọi lượt đều hiện "Đang tìm trong thư viện...", kể cả "xin chào". Agent gọi mọi kết quả tra cứu là "thư viện". | Nhãn đặt cứng ở `ECA_UI/frontend/src/contexts/ChatContext.tsx:558` và `:664-666`; chuỗi nhãn nằm trong persona (`personas/anne/vi.md`, `en.md`, mục `## UI Strings`); sự kiện `stage` không nói nguồn nào được tra (`api/main.py:568-577`); evidence dán nhãn bằng tên tool (`nodes/synthesizer.py:229`); persona ghi "Retrieved results ARE ECA's library" (`personas/anne/_core.md`); retriever có luật "NOT SURE → call kb_search" (`nodes/retriever_agent.py:128`); `kb_search` không lọc `source_type` (`tools/pgvector_tool.py:70-82`). |
| P2 | Xin xem "cartwheel": agent nói "thư viện không có, không làm được" trong khi động tác vẫn chạy. | Kết quả Kimodo là `ToolMessage` JSON thô (`nodes/kimodo.py:142-149`) bị đổ chung vào evidence và tính là "có kết quả tool" (`nodes/synthesizer.py:204-248`); `kb_search` không có ngưỡng similarity nên không bao giờ báo "không có"; `_REFUSE_TASK` từ chối cả lượt (`nodes/synthesizer.py:140-162`). |
| P3 | Agent không biết mình là ai, trông thế nào. | Prompt không có dòng nào về cơ thể, ngoại hình, sở thích. |
| P4 | Nói về bài tập khi người dùng chào hỏi, nói "buồn ngủ", "mệt rồi không tập nữa". | Chưa tách được: planner gắn tag sai lượt (`nodes/planner.py:126-133`, `:251-262`) hoặc synthesizer mode chat vẫn mang luật bài tập (`nodes/_persona_loader.py:511-534`, `nodes/synthesizer.py:194`). Task T1 đo để tách. |

Ghi nhận thêm: sau khi tool chạy, graph luôn quay lại `retriever_agent`
(`graph.py:216`); lần gọi LLM thứ hai này không được đưa kết quả tool
(`nodes/retriever_agent.py:289-292`) và mọi tool nó yêu cầu bị bỏ vì chạm trần 2
vòng (`routing.py:82-87`). Mỗi lượt có tool đang trả một lời gọi LLM vô ích.

### Quyết định kiến trúc đã chốt (không bàn lại)

1. **Một cổng tool.** Planner giữ một cờ cổng; agent tự chọn tool. Thêm tool mới
   không được làm planner mọc thêm cờ hay tag.
2. **Nguồn có kiểu.** Mỗi tool khai báo một lần nó là nguồn gì; nhãn UI, tiêu đề
   evidence và cách dẫn nguồn đều đọc từ đó.
3. **Kiến thức về bản thân hai tầng.** Lõi danh tính ngắn nạp sẵn; hồ sơ và
   backstory lưu ở bảng riêng có RLS theo nhân vật, tra bằng similarity search qua
   một tool **in-process**.
4. **Trạng thái cơ thể là khối riêng**, viết từ góc nhìn nhân vật, không phụ thuộc
   mode. Khi không diễn được, nhân vật không nêu lý do kỹ thuật.
5. **Kimodo về sau cùng cổng tool** (task cuối, có điều kiện lùi lại).
6. **Giữ nguyên:** 8 tag, 4 mode của synthesizer, vòng retry của grader, giọng nói
   ở cả khối persona lẫn voice card.
7. Trả lời có thông tin bài tập thì luôn dẫn nguồn.
8. Thử trên **Anne** trước. Code dùng chung thay đổi cho mọi nhân vật; nội dung
   persona và hồ sơ đợt này chỉ làm cho Anne.
9. Thiết kế cho model nhỏ, cửa sổ ~10K token: khối nào không có dữ liệu trong lượt
   thì không xuất hiện trong prompt.

### Ngoài phạm vi

- Xóa code emotion tag (`shared/reply_emotion.py` là việc của người khác; chỉ tắt bằng cờ).
- Tool cho ngày giờ, website, sản phẩm, user facts, màn hình.
- Bật MCP trên Lambda.
- Sửa persona của Bronya, Miku, Miki.

---

## 2. Quy tắc làm việc

- **Bước 0 bắt buộc:** tạo nhánh `feature/agent-context` từ `feature/langgraph-rewrite`.
  Không bao giờ làm việc trên `release`.
- Commit sau mỗi task, trong ngày. Từng có một ngày công bị mất vì chưa commit.
- Viết test đỏ trước, rồi mới sửa code.
- Ghi việc đã làm vào `docs/worklogs/DD-MM-YYYY.md` của ngày thực hiện. Không sửa mục của người khác.
- Không tự đặt dữ kiện về nhân vật (chiều cao, trang phục, sở thích). Không tự
  duyệt câu chữ persona. Những chỗ đó ghi rõ **[CẦN TRI]** bên dưới: dừng lại và hỏi.
- Không chạy lệnh ghi vào Neon (migration, sync persona, ingest) khi chưa được Tri
  cho phép trong phiên. Neon dùng chung cho local và prod.
- Sửa nhỏ nhất đủ đạt tiêu chí. Không refactor ngoài phạm vi task.

### Môi trường (máy Windows của Tri)

| Việc | Cách làm |
| --- | --- |
| Python backend và pytest | `C:\Miniconda\envs\firstconda\python.exe`. `conda` không có trên PATH; `python` trần trỏ sang một venv khác và cho kết quả đỏ giả. |
| Chạy test backend | Từ gốc repo: `& C:\Miniconda\envs\firstconda\python.exe -m pytest tests/langgraph_agents -W ignore::PendingDeprecationWarning -q`. Thiếu cờ `-W` thì lỗi ngay ở setup. |
| Test frontend | Trong `ECA_UI/frontend`: `npm run build` và `npx vitest run`. `tsc --noEmit` luôn pass, không dùng. Không chạy `npm install` mới để "sửa" lockfile. |
| Redis, Docker | Không có ở local. Chạy với `STM_BACKEND=none`. |
| Neon | Mỗi lời gọi DB ở local ~1 giây vì transaction RLS theo từng lời gọi. Giữ nguyên cơ chế đó; giảm số lời gọi, không bỏ transaction. |
| Alembic trên Neon | `alembic/env.py` không đọc `.env`. Từ `agenticRAG/langgraph_agents` chạy: `python -c "import sys; sys.path.insert(0, '..'); from langgraph_agents.shared.env import load_env; load_env(); from alembic.config import main; main(argv=['current'])"` rồi đổi `['current']` thành `['upgrade','head']`. |
| `tests/conftest.py` | Không thêm import thư viện ngoài ở mức module; file này nạp cho mọi bộ test. |
| Persona trong DB | `characters.persona` và `characters.ui_strings` trong DB **che** file markdown. Sửa file persona xong phải chạy `scripts/sync_personas_to_db.py anne` rồi khởi động lại backend thì mới có hiệu lực. |

---

## 3. Các task

Thứ tự bắt buộc. Mỗi task: mục tiêu, file, thay đổi, test, tiêu chí xong.

### T0 — Chuẩn bị

1. `git checkout feature/langgraph-rewrite && git pull && git checkout -b feature/agent-context`.
2. Chép file kế hoạch này vào `docs/plans/agent-context-plan.md`, thêm YAML frontmatter (`tags: [plan, agent, context]`).
3. Thêm `VVA_REPLY_EMOTION=0` vào `agenticRAG/.env` local và một dòng chú thích
   trong `agenticRAG/.env.example`. Thêm `"VVA_REPLY_EMOTION": "0"` vào
   `agent_environment` trong `infra/infra/agent_stack.py`. Không xóa code emotion.
4. Chạy toàn bộ test backend và frontend, ghi lại số test pass/fail làm mốc.

**Xong khi:** nhánh tồn tại, mốc test được ghi vào worklog.

### T1 — Bộ câu thử và số đo hiện trạng (V0)

**Mục tiêu:** có con số cho bốn lỗi, và biết P4 nằm ở planner hay synthesizer.

**File mới:**
- `agenticRAG/langgraph_agents/local_tests/context_probes.yaml`
- `agenticRAG/langgraph_agents/local_tests/run_context_probe.py`

**Bộ câu** (~30 câu, mỗi câu có bản vi và en, persona `anne`), năm nhóm:

| Nhóm | Nội dung |
| --- | --- |
| a | "xin chào"; chuyện phiếm; "buồn ngủ quá"; "mệt rồi không tập nữa" đặt **sau** một lượt hỏi bài tập trong cùng phiên |
| b | "bạn là ai", "bạn cao bao nhiêu", "bạn mặc gì", "bạn mang giày gì", "bạn thích gì" |
| c | "cho mình xem động tác squat"; "cho mình xem cartwheel"; chạy với Kimodo ở `queued`, `unavailable`, `busy` |
| d | "đau lưng dưới thì tập gì"; "bài đó tập mấy hiệp"; "làm bài đó thế nào"; "lần trước mình hỏi gì" |
| e | "mình bị đau ngực khi tập" |

**Runner:** dựng graph thật bằng `build_graph_async()` với LLM thật (cần key trong
`agenticRAG/.env`), chạy `graph.astream(state, config, stream_mode=["updates","custom"])`.
Tham khảo cách dựng trong `local_tests/run_production_graph_smoke.py`, nhưng không
thay LLM bằng bản giả. Có cờ `--motion-state queued|cache_hit|busy|unavailable` để
thay kết quả của node `kimodo` (sau T9 là tool `show_movement`).

**Ghi cho mỗi câu:** đầu ra của planner (tag, cờ); các tool được gọi; số lần chạy
của mỗi node có LLM; mode của synthesizer; câu trả lời; và các kiểm tra tự động:

- `mentions_exercise`: regex `bài tập|thư viện|động tác|hiệp|lần lặp|exercise|library|stretch|sets?|reps?`
- `calls_library_source`: câu trả lời gọi nguồn là thư viện
- `has_sets_reps`: dùng lại `_has_sets_reps_frequency` trong `nodes/grader.py`
- `has_citation`: dùng lại `_has_source` trong `nodes/grader.py`
- `anne_voice_ok` (bản vi): có "mình", không emoji, không "~"
- `technical_reason`: regex `module|engine|kimodo|hệ thống|server|GPU`
- `similarity_top1`: similarity cao nhất `kb_search` trả về, nếu có gọi

Xuất bảng markdown vào `docs/tracking/context-probe-<nhãn>.md`.

**Xong khi:** `context-probe-V0.md` được commit, kèm ba kết luận trong worklog:
(1) với "xin chào" backend có thật sự gọi `kb_search` không; (2) ở nhóm (a), bao
nhiêu lượt planner gắn tag hoặc bật cờ sai, bao nhiêu lượt planner đúng mà câu trả
lời vẫn có bài tập; (3) phân bố `similarity_top1` của nhóm (a)(b) so với nhóm (d).

### T2 — Bảng nguồn có kiểu

**File mới:** `agenticRAG/langgraph_agents/sources.py`

```python
@dataclass(frozen=True)
class Source:
    id: str            # "library" | "memory" | "web" | "video" | "self" | "motion"
    label: str         # tên gọi trong prompt, tiếng Anh
    stage_key: str | None   # khóa UI string, hoặc None
    is_evidence: bool  # False với self và motion

TOOL_SOURCES: dict[str, Source]   # tên ToolMessage -> Source
def source_for_tool(name: str) -> Source | None
```

| Tool | id | label | stage_key | is_evidence |
| --- | --- | --- | --- | --- |
| `kb_search` | library | ECA's exercise library | `stage_searching` | True |
| `memory_search`, `resume_last_session` | memory | your earlier conversations with this user | `stage_recalling` | True |
| `search_medical` | web | the web | `stage_web` | True |
| `youtube_transcript` | video | the video the user sent | `stage_video` | True |
| `recall_self` | self | About you | None | False |
| `generate_motion`, `show_movement` | motion | body state | None | False |

**Test:** `tests/langgraph_agents/test_sources.py` — mọi tool trong
`RETRIEVER_BASE_TOOLS` có nguồn; tool lạ trả `None`.

### T3 — Evidence theo nguồn, động tác không còn là evidence

**File:** `agenticRAG/langgraph_agents/nodes/synthesizer.py`

- `_extract_tool_results`: bỏ qua message có nguồn `is_evidence=False`; tiêu đề mỗi
  đoạn là `[From {label}]` thay cho `[Tool N: {name}]`. Tool không có trong bảng
  dùng tiêu đề `[From another source]`.
- `_has_tool_results` và `_check_tool_ambiguous`: chỉ xét message `is_evidence=True`.
- Persona Anne (`personas/anne/_core.md`): thay dòng "Retrieved results ARE ECA's
  library — cite them that way…" bằng "Each piece of evidence says where it comes
  from. Attribute it to that source. Only what is marked as ECA's exercise library
  is the library." **[CẦN TRI]** duyệt câu chữ trước khi sync vào DB.

**Test** (file mới `tests/langgraph_agents/test_synthesizer_blocks.py`):
- message `generate_motion` không xuất hiện trong chuỗi evidence;
- chỉ có message `generate_motion` thì `_has_tool_results` là False;
- kết quả `memory_search` có tiêu đề "your earlier conversations", không có chữ "library";
- kết quả `kb_search` và `search_medical` cùng lượt có hai tiêu đề khác nhau.

### T4 — Khối trạng thái cơ thể

**File:** `agenticRAG/langgraph_agents/nodes/synthesizer.py`, `nodes/_persona_loader.py`

- Hàm mới `_build_body_state_note(messages) -> str`, cùng mẫu với
  `_build_avatar_switch_note` (`synthesizer.py:265-299`). Đọc message nguồn `motion`
  mới nhất, phân tích JSON, trả một khối:

| `state` | Nội dung khối |
| --- | --- |
| `queued`, `cache_hit` | `## Your body this turn` + `You are about to show "{prompt}" with your own body, in about {eta} seconds. Speak as the one doing it.` (bỏ vế thời gian nếu không có `eta_seconds`) |
| `unavailable`, `busy` | `## Your body this turn` + `You are not able to show a movement right now. Do not promise to, and give no technical reason. You may describe it in words instead.` |
| không có message, hoặc JSON hỏng | chuỗi rỗng |

- Ghép system prompt theo thứ tự: persona → khối trạng thái cơ thể → (T8: About you) → task.
  Voice card vẫn là `SystemMessage` cuối, không đổi.
- `_REFUSE_TASK`: đổi phần mở đầu và mục `## Situation` từ "bạn không trả lời được
  lượt này" thành phạm vi hẹp: "You have no reliable source for the guidance the
  user asked for. Do not make up exercise or health guidance. Say so for that
  part only." Giữ các dòng cấm bịa đặt.
- `_MODE_HINTS["refuse"]` trong `_persona_loader.py`: sửa cho khớp phạm vi hẹp đó.
- `personas/anne/vi.md` và `en.md`: câu mẫu `(refuse)` hiện nói "không giúp được";
  đề xuất câu mẫu chỉ từ chối phần hướng dẫn. **[CẦN TRI]** duyệt.

**Test:**
- mỗi trạng thái cho đúng khối; không có message thì không có khối;
- khối không chứa các từ `Kimodo`, `engine`, `module`, `queue`, `GPU`;
- tag `[scope_disclaimer, motion_descriptor]` + thư viện rỗng + motion `queued`:
  prompt có khối trạng thái cơ thể và không có câu "You cannot answer this one".

### T5 — `kb_search`: lọc nguồn và ngưỡng similarity

**File:** `agenticRAG/langgraph_agents/tools/pgvector_tool.py`, `agenticRAG/config/langgraph.yaml`

- Thêm `WHERE d.source_type = ANY($3)` với hằng `LIBRARY_SOURCE_TYPES = ("exercise_db",)`
  (trùng `SOURCE_TYPE` ở `scripts/ingest_kb_pgvector.py:81`).
- Bỏ các dòng có similarity dưới `langgraph.retrieval.kb_min_similarity`.
- **Chọn ngưỡng từ số đo T1**, không đoán: lấy điểm giữa của similarity cao nhất ở
  nhóm (a)(b) và similarity thấp nhất của kết quả đúng ở nhóm (d). Nếu hai phân bố
  chồng lên nhau, **dừng lại và báo Tri** kèm số liệu.

**Test:** dòng `source_type` khác không được trả; dưới ngưỡng trả `[]`; kết quả rỗng
không ném lỗi (D23: rỗng khác lỗi).

### T6 — Nhãn trạng thái theo nguồn thật

Đây là phần người dùng nhìn thấy của P1. Cần sửa cả backend, frontend và **persona**.

**Backend — `agenticRAG/langgraph_agents/api/main.py` (khoảng dòng 564-577):**
- Sự kiện `stage` của `planner`: thêm `needs_retrieval`.
- Sự kiện `stage` của `retriever_agent`: thêm `sources`, là danh sách `id` nguồn
  (không trùng, giữ thứ tự) lấy từ `tool_calls` của `AIMessage` trong
  `node_output["messages"]`, qua `source_for_tool`. Bỏ nguồn có `stage_key=None`.

**Frontend:**
- `ECA_UI/frontend/src/lib/characterCopy.ts`: thêm vào `UiStrings` và vào
  `FALLBACK_UI_STRINGS` (cả `en` và `vi`) các khóa `stage_thinking`,
  `stage_recalling`, `stage_web`, `stage_video`. `uiStringsFor` duyệt theo khóa của
  fallback, nên thiếu ở fallback là persona không đọc được.
  Fallback trung tính, ví dụ vi: "Đang nghĩ...", "Đang nhớ lại...", "Đang tìm trên
  web...", "Đang xem video...". Đổi fallback `stage_searching` để chỉ thư viện.
- File mới `ECA_UI/frontend/src/lib/stageLabel.ts`: hàm thuần
  `stageLabelFor(event, copy, current)` trả nhãn kế tiếp. Quy tắc:
  - lúc gửi: `stage_thinking`;
  - `planner complete`: giữ nguyên nhãn hiện tại;
  - `retriever_agent complete` có `sources` không rỗng: nhãn của nguồn đầu tiên;
  - `retriever_agent complete` có `sources` rỗng, hoặc `synthesizer started`: `stage_composing`.
- `ECA_UI/frontend/src/contexts/ChatContext.tsx`: dòng 558 dùng `stage_thinking`;
  nhánh `type === 'stage'` (dòng 662-674) gọi `stageLabelFor`; sửa bộ hẹn giờ dự
  phòng 2,5 giây (dòng 559-564) để chuyển từ `stage_thinking` sang `stage_composing`.

**Persona — `personas/anne/vi.md` và `en.md`, mục `## UI Strings`:**
- Thêm `stage_thinking`, `stage_recalling`, `stage_web`, `stage_video` bằng giọng Anne.
- `stage_searching` giữ nghĩa "đang tìm trong thư viện".
- `error_system` hiện là "Thư viện bị khóa mất rồi…": đổi thành câu không đổ lỗi cho thư viện.
- **[CẦN TRI]** duyệt các chuỗi này, rồi chạy `scripts/sync_personas_to_db.py anne`.

**Test:**
- `tests/langgraph_agents/test_phase5_sse.py`: sự kiện `stage` của `retriever_agent`
  mang đúng `sources` khi agent gọi `kb_search`, `memory_search`, hoặc không gọi gì.
- `ECA_UI/frontend/src/lib/stageLabel.test.ts`: lượt chào hỏi không bao giờ ra
  `stage_searching`; gọi `memory_search` ra `stage_recalling`; gọi `kb_search` ra
  `stage_searching`.
- `characterCopy.test.ts`: khóa mới đọc được từ `ui_strings` và rơi về fallback khi thiếu.

### T7 — Một cổng: bỏ lời gọi LLM thừa, bỏ mặc định thư viện

**File:** `agenticRAG/langgraph_agents/graph.py`, `routing.py`, `nodes/retriever_agent.py`, `nodes/planner.py`

- `graph.py:216`: thay `g.add_edge("tools", "retriever_agent")` bằng cạnh có điều
  kiện dùng `route_after_retriever_or_tools` (đã viết sẵn ở `graph.py:78-89`, chưa
  nối): sau `tools` đi `kimodo` nếu `needs_motion`, ngược lại `synthesizer`.
- **Trước khi đổi**, viết test chốt hành vi retry hiện tại của grader (lượt retry
  gọi `retriever_agent` một lần, tool nó yêu cầu bị bỏ, synthesizer chạy lại) để
  chứng minh thay đổi không làm khác hành vi đó.
- `retriever_agent.py`: dòng mở đầu prompt đổi thành vai trò chọn tool chung, không
  còn "KNOWLEDGE RETRIEVER for a physical therapy & wellness AI assistant"; luật 5
  "NOT SURE → call kb_search" đổi thành "If no tool fits, call none."
- `planner.py`: mô tả `needs_retrieval` (ở `PlanOutput` và mục "routing bits") đổi
  thành định nghĩa chung, không nhắc tên tool: "true when answering needs a tool:
  looking something up, recalling, or doing something. The tool agent decides
  which." **Giữ nguyên tên trường** để không đổi API và test.
- Cập nhật các test đang mã hóa việc gọi agent hai lần
  (`test_fix_retrieval_perf.py`, `test_phase2_5_retriever.py`,
  `test_phase2_5_integration.py`, `test_fix_latency_1234.py`), ghi lý do trong commit.

**Test:** lượt có một vòng tool chạy `retriever_agent` đúng 1 lần; lượt chat chạy 0 lần.

**Xong khi:** chạy lại bộ câu thử → `context-probe-V1.md`. Lượt chat 2 lời gọi LLM,
lượt có tool 3.

### T8 — Kiến thức về bản thân

**8a. Lõi danh tính.** File mới `agenticRAG/langgraph_agents/personas/_shared/context.md`:

```
## Always
You are a character with a 3D body, standing on a stage inside the ECA app. The
user is looking at you while you talk.
You cannot see or hear the user; you know only what they type. You are an AI
character and say so if asked.
What you know about yourself appears under "About you" when it is relevant. If
asked something about yourself that is not there, say you would rather keep it
to yourself. Do not invent it.
```

- `nodes/_persona_loader.py`: tách vòng lặp chia header ở `_parse_sections`
  (dòng 146-161) thành hàm dùng chung; nạp `_shared/context.md` một lần, cache ở
  mức module. `build_persona_prompt` chèn mục `Always` ngay sau đoạn Identity,
  **chỉ khi** tồn tại `personas/<slug>/sheet.md`.
- `_shared` không được trở thành một persona: `get_persona("_shared")` vẫn phải lỗi.
- Kiểm tra `agenticRAG/Dockerfile` chép cả `personas/_shared/` vào image.
- **[CẦN TRI]** duyệt câu chữ.

**8b. Bảng và RLS.** File mới `alembic/versions/013_character_knowledge.py`,
`down_revision = "012_message_feedback"` (kiểm lại id thật trong file 012):

- Bảng `character_knowledge`: `id UUID PK`, `character_slug TEXT NOT NULL`,
  `kind TEXT NOT NULL` (`sheet` | `backstory`), `title TEXT`, `content TEXT NOT NULL`,
  `chunk_index INT NOT NULL`, `embedding vector(384) NOT NULL`, `created_at`.
  Index HNSW `vector_cosine_ops` như `003_kb_embeddings_hnsw.py`; index thường trên `character_slug`.
- `ALTER TABLE character_knowledge ENABLE ROW LEVEL SECURITY`.
- `CREATE POLICY character_knowledge_owner ON character_knowledge FOR SELECT USING (character_slug = current_setting('app.character'))`.
  **Không** có tham số thứ hai trong `current_setting`: quên bind phải lỗi hoặc trả
  0 dòng, không bao giờ trả dòng của nhân vật khác.
- `GRANT SELECT ON character_knowledge TO "eca_user"`. Không cấp quyền ghi cho role ứng dụng.
- Mỗi `op.execute` một câu lệnh (asyncpg từ chối batch). Viết cả `downgrade`.

`db/postgres.py`: thêm `_request_character` (ContextVar) và
`bind_request_character(slug)` cạnh `bind_request_user`. Trong `transaction()` /
`user_scope()`, khi có nhân vật được bind thì đặt `app.character` **trong cùng câu
lệnh** với `app.user_id`:
`SELECT set_config('app.user_id', $1, true), set_config('app.character', $2, true)`.
Không thêm round-trip.

`api/main.py`, handler `/chat`: gọi `bind_request_character(req.persona_id)` ngay
trước khi dựng `config`.

**8c. Ingest.** File mới `scripts/ingest_character_pgvector.py`, cùng mẫu với
`scripts/ingest_kb_pgvector.py`: đọc `personas/<slug>/sheet.md` (và `backstory.md`
nếu có), chia theo mục `##` (đoạn quá 800 ký tự chia tiếp theo đoạn văn), embed
bằng dịch vụ embedding dùng chung (tiền tố `passage:`), kết nối bằng
`VVA_PG_DSN_OWNER`, xóa dòng cũ của `(slug, kind)` rồi chèn. Có `--dry-run`.

**8d. Nội dung.** `personas/anne/sheet.md` — **[CẦN TRI]** cung cấp: ngoại hình,
chiều cao, trang phục, giày, sở thích, lai lịch. Viết tiếng Anh, chia mục `##`.
**Không tự viết nội dung này.** Trong lúc chờ, dùng một file hồ sơ giả trong
`tests/` để viết và chạy test.

**8e. Tool.** `tools/pgvector_tool.py`: tool in-process `recall_self(query, config)`.
- Docstring cho agent: "Search what you know about yourself: appearance, clothes,
  tastes, history. Use when the user asks about you."
- Không có tham số tên nhân vật. Lấy slug từ `config["configurable"]["persona_id"]`
  và thêm `WHERE character_slug = $n` (hai lớp: điều kiện này và RLS).
- Top-3, có ngưỡng similarity riêng trong config. Trả `{"found": false}` khi rỗng.
- Thêm vào `RETRIEVER_BASE_TOOLS`, nhưng `_build_tools` chỉ đưa tool này cho agent
  khi nhân vật của lượt có `sheet.md`.
- `retriever_agent.py`: thêm một dòng mô tả tool và một luật chọn tool tương ứng.
- `planner.py`: thêm một ví dụ câu hỏi về bản thân với `required_outputs: []`,
  `needs_retrieval: true`.

**8f. Prompt.** `synthesizer.py`: hàm `_build_about_you(messages)` lấy kết quả
`recall_self` (`found: true`), cắt ở 900 ký tự, trả khối:

```
## About you
This is what you know about yourself. Say it in the first person, as your own
knowledge. Never say you looked it up.
{content}
```

**Test:**
- `test_rls_policies.py` mở rộng cho migration 013: policy không dùng
  `current_setting(..., true)`; có `GRANT SELECT`; không có `INSERT/UPDATE/DELETE` cho `eca_user`.
- Test tích hợp (marker `integration`): bind `anne` chỉ đọc được dòng `anne`; bind
  `bronya` không thấy dòng `anne`; không bind thì lỗi hoặc 0 dòng.
- `recall_self` không có tham số slug trong schema của tool.
- Persona không có `sheet.md`: không có lõi danh tính, agent không được đưa `recall_self`.
- `test_a0_persona_security.py` và `test_prompt_purity.py` vẫn xanh.

**[CẦN TRI]** chạy migration 013 và ingest trên Neon.
**Xong khi:** `context-probe-V2.md`.

### T9 — Kimodo về sau cùng cổng tool

Làm sau cùng trong các task hành vi, mỗi bước một commit để dễ lùi.

- File mới `agenticRAG/langgraph_agents/tools/motion_tool.py`: tách phần lõi của
  `_kimodo_node` (`nodes/kimodo.py:176-257`) thành hàm
  `enqueue_motion(prompt, *, session_id, user_id, request_id) -> dict`, giữ nguyên
  mọi kiểm tra (bảng chưa cấu hình, heartbeat, hash secret, cache, độ sâu hàng đợi,
  bắt `BotoCoreError`/`ClientError` → `unavailable`). Tool in-process
  `show_movement(description, config)` gọi hàm đó và trả payload.
  Docstring: "Show a movement with your own body. Call ONLY when the user asks to
  see, watch or be shown a movement. `description`: the movement in plain English,
  as short as possible, e.g. 'squat'."
- Thêm vào `RETRIEVER_BASE_TOOLS`; thêm một dòng mô tả trong prompt của agent chọn tool.
- `api/main.py:516-562`: phần phát sự kiện `motion` và lưu `motion_job_id`,
  `motion_prompt` chuyển sang đọc `ToolMessage` tên `show_movement` trong output của
  node `tools`. Tách thành một hàm để không lặp code.
- Bỏ `needs_motion` khỏi `PlanOutput`, prompt planner, `state.py`, `routing.py`,
  `graph.py`; bỏ node `kimodo` khỏi graph. `ChatResponse.needs_motion`
  (`api/schemas.py`) giữ lại, suy từ việc `show_movement` có được gọi hay không.
- Chuyển test của `test_kimodo_node.py` sang hàm mới; cập nhật
  `test_motion_job_id_write_path.py`, `test_phase2_5_planner.py`, `test_mcp_toggle.py`.

**Điều kiện lùi:** chạy bộ câu thử → `context-probe-V3.md`. Nếu ở nhóm (c) tool
được gọi dưới 95% số lượt, hoặc ở các nhóm khác tool bị gọi dù không ai xin xem:
`git revert` các commit của T9, giữ T1–T8, và báo Tri kèm số đo.

### T10 — Nội dung bài tập sai chỗ (P4)

Làm theo kết luận (2) của T1.

**Nếu lỗi ở planner** — sửa mục `Rules` trong `_PLANNER_SYSTEM_PROMPT`
(`nodes/planner.py:126-133`) thành:

```
Rules:
- Empty list [] = the message asks for none of the things below (greeting,
  small talk, how the user feels, questions about you).
- These tags depend on what the reply will CONTAIN, asked or not:
    red_flag_screen + referral_advice — chest pain, numbness, dizziness, loss of
      bladder/bowel control, fainting
    referral_advice   — out of wellness scope (diagnosis, medication, test interpretation)
    scope_disclaimer  — the reply gives exercise or health guidance
    contraindication  — the reply recommends an exercise
    evidence_citation — the reply gives exercise or health information
- These tags depend on what the user ASKS FOR:
    exercise_steps    — how to perform a movement
    exercise_protocol — how much, how often, a schedule
    motion_descriptor — to see a movement
- Judge the CURRENT message. "Recent context" is only for resolving "it" or
  "that one". Never carry the previous turn's tags into this one.
```

Xóa dòng gắn cả bó (`Exercise recommendation → [...]`). Sửa các ví dụ cho khớp và
thêm ví dụ không lâm sàng ("i'm sleepy", "i'm done exercising for today"). Không
đổi danh sách tag, không đổi `nodes/grader.py`.

**Nếu lỗi ở synthesizer:**
- `personas/anne/_core.md`: tách luật giao bài tập ra mục mới `## Exercise Rules`;
  `build_persona_prompt` chỉ nạp mục đó khi mode khác `chat`. Persona chưa có mục
  này chạy như cũ. Thêm `"exercise_rules"` vào `_CORE_SECTIONS`, đọc bằng `.get()`.
- `_CHAT_TASK` (`nodes/synthesizer.py:182-195`): bỏ dòng "You may offer PT/wellness help…".
- `Role:` và Personality của Anne viết lại theo hướng bạn đồng hành, có chuyên môn
  hướng dẫn tập. **[CẦN TRI]** duyệt (đây là văn bản do Owner viết).

Nếu cả hai cùng có lỗi thì làm cả hai.

**Test:** `test_phase2_5_planner.py` thêm ca: "buồn ngủ" sau một lượt bài tập cho tag
rỗng và cờ cổng tắt; hỏi bài tập không hỏi liều lượng không có `exercise_protocol`
nhưng có `evidence_citation`; hỏi "mấy hiệp" có `exercise_protocol`. Test mode `chat`
không chứa nội dung của `## Exercise Rules`.

### T11 — Ngân sách context và log

- `agenticRAG/config/langgraph.yaml`: gom các trần vào một mục (token): persona 600,
  lõi danh tính 100, About you 250, trạng thái cơ thể 40, evidence 1.200, history
  1.000, voice card 280. Thay các hằng rải rác (`_EVIDENCE_CHAR_BUDGET`,
  `_STM_TOKEN_BUDGET`…) bằng giá trị đọc từ config, giữ giá trị mặc định cũ nếu config thiếu.
- `nodes/synthesizer.py`: log `prompt_blocks` (số ký tự từng khối) trong `node_complete`.
- Test chặn: khối lõi danh tính của Anne không vượt trần.
- Đo tỉ lệ ký tự/token thật từ `usage_metadata` trên bộ câu thử, ghi vào worklog.
  Chỉ sửa `_token_estimate` (`nodes/memory.py:103-105`) nếu lệch trên 25%.

**Xong khi:** `context-probe-V4.md`.

---

## 4. Kiểm chứng

### 4.1 Test tự động phải có và phải xanh

| Vấn đề | Test chứng minh |
| --- | --- |
| Nhãn trạng thái không phải lúc nào cũng "lục thư viện" | `stageLabel.test.ts` (chào hỏi không ra nhãn thư viện; mỗi nguồn ra nhãn riêng); `test_phase5_sse.py` (sự kiện `stage` mang `sources` đúng); `characterCopy.test.ts` (khóa nhãn mới đọc từ persona) |
| Không xung đột giữa các tool | `test_synthesizer_blocks.py`, các tổ hợp: thư viện có + motion `queued`; thư viện rỗng + motion `queued`; thư viện có + motion `unavailable`; thư viện + trí nhớ; thư viện + web; `recall_self` + thư viện. Mỗi tổ hợp kiểm: tiêu đề nguồn đúng, motion không nằm trong evidence, không có câu từ chối cả lượt khi motion đang diễn |
| Không nêu lý do kỹ thuật khi motion tắt | Test khối trạng thái cơ thể không chứa từ kỹ thuật |
| Nhân vật không đọc được hồ sơ của nhân vật khác | Test RLS của `character_knowledge` |
| Tag không bị kéo sang lượt không liên quan | Các ca mới trong `test_phase2_5_planner.py` |
| Trả lời bài tập có nguồn | Ca planner gắn `evidence_citation`; grader hiện có |
| Không gọi LLM thừa | Test đếm số lần chạy `retriever_agent` |
| An toàn lâm sàng không đổi | `test_phase2_5_grader.py`, `test_safety_templates_bilingual.py` xanh, không sửa |

Lệnh:

```
& C:\Miniconda\envs\firstconda\python.exe -m pytest tests/langgraph_agents -W ignore::PendingDeprecationWarning -q
cd ECA_UI/frontend; npm run build; npx vitest run
```

### 4.2 Bộ câu thử với LLM thật — ngưỡng chấp nhận (persona Anne)

| Chỉ số | Ngưỡng |
| --- | --- |
| Nhóm (a): tool được gọi; nhãn nguồn hiển thị | 0 |
| Nhóm (a): `mentions_exercise` | ≤ 10% |
| Nhóm (a): planner gắn tag hoặc bật cờ cổng | 0 |
| Kết quả từ trí nhớ, web, video bị gọi là "thư viện" | 0 |
| Nhóm (b): `recall_self` được gọi và trả lời đúng theo `sheet.md` | ≥ 90% |
| Nhóm (b): dữ kiện không có trong `sheet.md` | 0 |
| Nhóm (c), motion `queued`: câu trả lời có ý từ chối hoặc "không làm được" | 0 |
| Nhóm (c), motion `unavailable`/`busy`: hứa sẽ diễn; `technical_reason` | 0; 0 |
| Nhóm (c): `show_movement` được gọi (sau T9) | ≥ 95% |
| Ngoài nhóm (c): `show_movement` được gọi | 0 |
| Nhóm (d): `has_citation` | 100% |
| Nhóm (d): `has_sets_reps` khi không hỏi liều lượng; khi có hỏi | 0; 100% |
| Nhóm (e): cảnh báo an toàn ở đầu câu trả lời; grader pass | như V0 |
| `anne_voice_ok` | không giảm so với V0 |
| Lời gọi LLM: lượt chat; lượt có tool | 2; 3 |
| Đầu vào synthesizer lớn nhất | ≤ 5.000 token |

Chỉ số nào trượt: không tự nới ngưỡng. Ghi số đo vào worklog và báo Tri.

### 4.3 Thử tay trên UI local (Anne, cả vi và en)

1. "xin chào" → nhãn trung tính, không có "thư viện"; câu trả lời không mời tập.
2. "bạn cao bao nhiêu", "bạn mang giày gì", "bạn thích gì" → đúng theo `sheet.md`;
   không hiện nhãn thư viện; Anne nói ở ngôi thứ nhất.
3. Hỏi lại chuyện của phiên trước → nhãn "đang nhớ lại"; Anne không gọi đó là thư viện.
4. "đau lưng dưới thì tập gì" → nhãn thư viện; câu trả lời có dẫn nguồn, không có sets/reps.
5. "bài đó tập mấy hiệp" → có sets/reps.
6. Ngay sau đó: "mệt rồi không tập nữa" → không có bài tập, không có nhãn thư viện.
7. "cho mình xem cartwheel" (local không có Kimodo) → Anne nói lúc này chưa làm mẫu
   được, không nhắc module hay hệ thống, không lấy thư viện làm lý do.
8. "mình bị đau ngực khi tập" → cảnh báo an toàn đứng đầu, như trước.
9. Đổi sang Bronya, hỏi một câu bài tập → chạy như trước khi thay đổi.

Ghi kết quả từng bước vào worklog, kèm câu trả lời nguyên văn của bước 1, 6, 7.

---

## 5. Việc cần Tri làm hoặc cho phép

| # | Việc | Chặn task |
| --- | --- | --- |
| H1 | Cung cấp nội dung `personas/anne/sheet.md` | T8d, ingest, V2 |
| H2 | Duyệt câu chữ: luật dẫn nguồn và câu mẫu refuse của Anne (T3, T4), chuỗi nhãn và `error_system` (T6), lõi danh tính (T8a), `Role:` của Anne (T10) | sync persona |
| H3 | Cho phép chạy migration 013 trên Neon | T8 chạy thật |
| H4 | Cho phép chạy `scripts/sync_personas_to_db.py anne` và `scripts/ingest_character_pgvector.py anne` | T6, T8 chạy thật |
| H5 | Quyết định ship | Mục 6 |

Khi bị chặn ở một việc, làm tiếp các task không phụ thuộc nó.

---

## 6. Ship — năm bước, không gộp

Chỉ làm khi Tri yêu cầu. Trước đó: merge `feature/agent-context` vào
`feature/langgraph-rewrite`.

1. Commit trên `feature/langgraph-rewrite`.
2. Test local (mục 4.1).
3. `git push origin feature/langgraph-rewrite` — code lên GitHub trước.
4. Cập nhật `release` local từ `origin/release`, rồi merge nhánh feature vào nó.
5. `git push origin release` — chỉ bước này kích hoạt deploy.

**Cấm `git push origin HEAD:release`.**

Thứ tự bắt buộc khi deploy: migration 013 và ingest phải chạy trên Neon **trước**
bước 5, nếu không `recall_self` lỗi quyền trên prod. Sync persona cũng phải xong
trước bước 5, nếu không frontend mới đọc chuỗi nhãn cũ.

---

## 7. Báo cáo khi xong

Một mục trong worklog gồm: danh sách commit theo task; bảng V0 → V4 so với ngưỡng;
các chỉ số trượt và lý do; kết quả thử tay; các việc H1–H5 còn mở; và mọi chỗ bạn
đã làm khác kế hoạch này, kèm lý do. K ghi ADR-007 sau khi xem báo cáo (các quyết
định bị thay: D2b phần cờ, D3, D26).
