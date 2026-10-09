# LangGraph Flow + Persona Integration

> Viết 11-08-2026. Làm context để add PERSONA cho character.
> Cập nhật 04-10-2026 (grader-contract): hai chỗ kiểm, grader LLM chấm,
> câu an toàn + dòng nguồn do code phát, retry viết thêm phần thiếu.
> Cập nhật 09-10-2026 (stage-labels): nhãn trạng thái theo tin backend
> (thinking → nhãn nguồn → synthesizer started → chữ, không đồng hồ);
> khối "Your body this turn" thêm câu giọng nói khi lượt đọc thành tiếng.

---

## 1. Tổng quan 8 node

```
planner → retriever_agent → tools → [kimodo] → synthesizer → grader → END
              ↑                │                    ↑           │
              └── kiểm 1 ──────┘                    └── kiểm 2 ─┘
```

Kiểm 1 (sau `tools`, không tốn LLM): không gọi tool hoặc tool lỗi thì quay lại
`retriever_agent`, tối đa một lần (`routing.retrieval_fault`). Kết quả rỗng
không phải lỗi (D24). Tool ném lỗi thành `ToolMessage {"error": ...}`, lượt
không chết.

Kiểm 2 (trong `grader`, một lời gọi LLM thinking-disabled): từng mục kiểm có
trong evidence mà câu trả lời thiếu thì quay lại `synthesizer` viết thêm phần
thiếu, tối đa một lần. Câu an toàn và dòng nguồn do code phát thẳng vào luồng
stream — model không tự viết.

```
START
  │
  ▼
┌─────────┐     0 LLM calls
│ memory  │     Assemble context: user_facts + summary chunks + recent raw
└────┬────┘     Ghi vào state.messages (SystemMessage + chat history)
     │
     ▼
┌─────────┐     1 LLM call (fast model)
│ planner │     Phân tích intent → 3-axis PlanOutput:
└────┬────┘       - required_outputs: list[str] tag (8 tag trong vocab)
     │            - resolved_query:    câu hỏi đã resolve coreference
     │            - needs_retrieval:   bool → cổng RETRIEVER
     │            - needs_motion:      bool → hard edge Kimodo
     │            - needs_clarification: bool
     │
     ├── needs_clarification ──────────────┐
     ├── needs_retrieval ─────┐            │
     ├── needs_motion (only) ─┐│            │
     ├── else ────────────────┐││            │
     │                        ▼▼▼            ▼
     │                   ┌────────────┐  ┌──────────────┐
     │                   │ retriever  │  │  synthesizer │  (skip thẳng)
     │                   │  _agent    │  │              │
     │                   └─────┬──────┘  └──────────────┘
     │                         │
      │                    ┌────┴────┐
      │                    ▼         │ kiểm 1: no_tool_called → quay lại
      │               ┌────────┐    │ retriever (tối đa 1 lần);
      │               │ tools  │────┘ tool_error → quay lại retriever
      │               └───┬────┘    │ (tối đa 1 lần); rỗng đi tiếp (D24)
      │                   │ done → route kiểm tra motion tag
     │                   │
     │      ┌────────────┘
     │      ▼
     │ ┌─────────┐     0 LLM calls (MCP subprocess)
     │ │ kimodo  │     Gọi Kimodo GPU server → sinh 3D motion
     │ └────┬────┘     Chỉ chạy khi needs_motion=true (hard edge D3/D26)
     │      │           Ghi ToolMessage vào state.messages
     │      │
     │      ▼
     │ ┌──────────────┐  1 LLM call (heavy model) ← ⚡ PERSONA ÁP VÀO ĐÂY
     │ │  synthesizer │  Universal responder — 4 mode derive từ signals:
     │ └──────┬───────┘    clarify | refuse | synthesize | chat
     │        │            Stream SSE từng token qua writer()
     │        │            Output: state.final_answer
     │        │
     │        ├── required_outputs != [] ──┐  ← CỔNG GRADER (D15)
     │        ├── required_outputs == [] ──┼── END (fast-path)
     │        │                            │
     │        │              ┌─────────────┘
      │        │              ▼
      │        │       ┌─────────┐   1 LLM call (judge, thinking disabled)
      │        │       │ grader  │   LLM chấm từng mục kiểm:
      │        │       └────┬────┘     - Thiếu mà evidence có → retry viết thêm
      │        │            │          - Thiếu mà evidence im → pass
      │        │            │          - Dòng kết do code nối (referral, nguồn,
      │        │            │            disclaimer), không regex
      │        │            │
      │        │    retry ──┘─── quay lại synthesizer (chỉ nối phần thiếu)
     │        │
     │        ▼
     │      END
     │
     ▼
┌────────────────┐
│ error_handler  │  CRITICAL error ở bất kỳ node nào → node này → END
└────────────────┘
```

---

## 2. Chi tiết từng node

### 2.1 memory — `nodes/memory.py`

| | |
|---|---|
| LLM calls | 0 |
| Input | config.user_id, session_id |
| Output | `state.messages` — prepend SystemMessage + recent chat history |
| Persona | ❌ Không liên quan |

**Logic**:
1. Load `user_facts` từ PostgreSQL `user_memory` table (Tier 1, always-on)
2. Load `summary_chunks` từ PostgreSQL `summaries` table (frozen, append-only)
3. Load `recent_raw` từ Redis cache (fallback DB), budget 1500 tokens
4. Assemble SystemMessage: `[USER FACTS]` + `[SESSION HISTORY]`
5. Append recent chat as HumanMessage/AIMessage

### 2.2 planner — `nodes/planner.py`

| | |
|---|---|
| LLM calls | 1 (fast model: DeepSeek, fallback Gemini) |
| Input | state.messages + config.query |
| Output | PlanOutput: required_outputs, resolved_query, needs_retrieval, needs_motion, needs_clarification |
| Persona | ❌ Không liên quan |

**8 tag trong vocabulary** (PLANNER_TAGS = TAG_RULES keys):
- Safety: `red_flag_screen`, `referral_advice`, `scope_disclaimer`
- Quality: `exercise_protocol`, `exercise_steps`, `contraindication`, `evidence_citation`, `motion_descriptor`

**3-axis model (D1)**: Không còn intent enum 6 loại nữa. Planner = manager chỉ định WHAT (tags + routing bits), không HOW.

**Fallback**: Nếu LLM fail → `needs_clarification=true`, không tag, không retrieval.

### 2.3 retriever_agent ⇄ tools — `nodes/retriever_agent.py`

| | |
|---|---|
| LLM calls | 1-2 (fast model, bind_tools) |
| Input | resolved_query + required_outputs |
| Output | ToolMessage từ các tool đã gọi |
| Persona | ❌ Không liên quan |

**Retriever = dev tự chọn HOW** (D2b). Planner chỉ nói WHAT.

**Tools available** (RETRIEVER_BASE_TOOLS):
- `kb_search` — pgvector semantic search trong internal KB
- `memory_search` — search past conversation summaries  
- `resume_last_session` — load session cũ
- `youtube_transcript` — lấy transcript YouTube link
- `search_medical` — web search (MCP), chỉ khi `web_search=true`

**Loop rule (kiểm 1)**: `retriever_agent` chạy tối đa 2 lần
(`MAX_RETRIEVER_ROUNDS=2`). Không gọi tool → quay lại một lần với khối
`## Second attempt`; tool lỗi → quay lại một lần; kết quả rỗng đi tiếp.
Tool calls được gọi song song.

**P3 execution guard** (`_make_guarded_tools_node`): Nếu `web_search=false` mà retriever vẫn gọi `search_medical` → chặn cứng, trả ToolMessage `{"blocked": "web_search_disabled"}`.

### 2.4 kimodo — `nodes/kimodo.py`

| | |
|---|---|
| LLM calls | 0 |
| Input | resolved_query |
| Output | ToolMessage với motion result |
| Persona | ❌ Không liên quan |

Hard edge từ planner (D3/D26): chỉ chạy khi `needs_motion=true`. Gọi Kimodo MCP server qua `generate_motion` tool. Nếu MCP server down → RECOVERABLE error (graceful degradation).

### 2.5 synthesizer — `nodes/synthesizer.py` ← ⚡ PERSONA SỐNG Ở ĐÂY

| | |
|---|---|
| LLM calls | 1 (heavy model: DeepSeek, fallback Gemini) |
| Input | state.messages (context + tool results) + resolved_query + required_outputs + persona_id |
| Output | state.final_answer (stream SSE) |
| Persona | ✅ **YES — D30: persona áp vào ALL 4 mode** |

#### 2.5.1 Mode derivation (D29 — emerge from signals, không enum)

```python
def _derive_mode(state) -> str:
    if needs_clarification or tool_ambiguous → "clarify"
    elif clinical/safety tags + no tool results → "refuse"  
    elif has tool results                         → "synthesize"
    else                                          → "chat"
```

#### 2.5.2 Cách persona được inject

```python
# synthesizer.py:293-295
persona = get_persona(persona_id)          # load từ personas/*.md, cache
persona_system = build_persona_prompt(persona, mode)  # tạo system prompt
system = f"{persona_system}\n\n---\n\n{task_system}"  # ghép với task prompt
```

**Prompt structure gửi vào LLM**:
```
[PERSONA BLOCK]              ← từ persona MD, áp cho mọi mode
  - Identity (tên, role)
  - Personality (tone, formality)
  - Behavioral Rules
  - Response Formatting
  - Turn hint (1 dòng mode-specific)

---
[TASK BLOCK]                 ← thay đổi theo mode
  - LANGUAGE_RULE (luôn có)
  - Required deliverables (chỉ tag do model viết)
  - Retrieved evidence (tool results)
  - User's question
  - Contract note (câu đã hiện + dòng sẽ thêm, nếu có tag do code viết)
```

Câu mở đầu (`red_flag_screen`) do code phát vào stream trước chunk LLM đầu;
`final_answer` = câu mở đầu + chữ LLM. Lượt viết thêm chỉ nối phần thiếu,
không viết lại. Bất biến: chuỗi đã stream bằng đúng `final_answer`.

Nhãn trạng thái trên giao diện đi đúng thứ tự tin backend gửi: thinking lúc
gửi → nhãn của mọi nguồn đã tra (chỉ ở lượt có tra cứu) → composing từ lúc
synthesizer báo bắt đầu (ngay khi node chạy, không đợi chữ đầu tiên của LLM)
→ chữ. Không đồng hồ dự phòng: lượt không tra nguồn nào giữ nhãn hiện tại
tới khi synthesizer bắt đầu.

Khối "Your body this turn" trong prompt gồm dòng cử động (nếu có) và câu
giọng nói "You are speaking this reply aloud in your own voice; the user
hears you." khi lượt này được đọc thành tiếng (`speaks_aloud` từ
`output_mode` + TTS bật). Rỗng khi không có gì để nói.

#### 2.5.3 4 mode prompt templates

| Mode | Task template | Dùng khi |
|------|--------------|----------|
| `synthesize` | `_SYNTHESIZE_TASK` | Có tool results → trả lời dựa trên evidence |
| `refuse` | `_REFUSE_TASK` | Clinical tag + không source → từ chối, khuyên đi bác sĩ |
| `clarify` | `_CLARIFY_TASK` | Thiếu thông tin → hỏi lại user |
| `chat` | `_CHAT_TASK` | Chào hỏi, casual → trả lời ngắn gọn |

### 2.6 grader — `nodes/grader.py` + `tag_contract.py`

| | |
|---|---|
| LLM calls | 1 (judge, role `grader`: thinking disabled, timeout 5s, no retry) |
| Input | state.final_answer (bỏ câu mở đầu) + checklist `check_items` + evidence |
| Output | grader_result: "pass" / "retry" / "pass_with_warning" + `grader_detail` |
| Persona | ❌ Không liên quan trực tiếp (câu chữ lấy từ persona overlay qua `get_safety_text`) |

**Cơ chế**: LLM chấm từng mục kiểm (`_judge`), không regex. Tám hàm `_has_*`
cũ giữ lại làm công cụ đo (`scripts/eval_grader_rules.py`).

**Bảng 8 tag** (`TAG_CONTRACT` — ai viết, vị trí, mục kiểm):

| Tag | Ai viết | Vị trí | Mục kiểm |
|---|---|---|---|
| `red_flag_screen` | code | mở đầu | — (câu an toàn do code phát) |
| `referral_advice` | code | kết | — |
| `evidence_citation` | code | kết | — (dòng nguồn do code phát) |
| `scope_disclaimer` | code | kết | — |
| `exercise_protocol` | model | thân | `amount` (sets/reps/giữ bao lâu), `frequency` (mỗi ngày/tuần) |
| `exercise_steps` | model | thân | ≥2 bước có thứ tự |
| `contraindication` | model | thân | ai không nên tập / cần cẩn trọng |
| `motion_descriptor` | model | thân | chuyển động + khớp liên quan |

**Flow grader**:
1. `required_outputs=[]` → skip (không gọi grader, routing xử lý ở D15)
2. Thân rỗng → `retry` một lần, không chấm
3. `retry_count>=1` → không chấm lại → `_finish` nối dòng kết
4. Không evidence hits → `pass`, mọi mục `no_evidence`
5. LLM chấm lỗi/JSON hỏng → `pass` (log `grader_judge_failed`)
6. Thiếu mà evidence có (`synth_missed`) → `retry`, `grader_feedback` là các
   định nghĩa mục thiếu → synthesizer viết thêm
7. Thiếu mà evidence im (`source_silent`) → `pass`
8. `_finish` nối dòng kết (referral → dòng nguồn → disclaimer) vào stream và
   `final_answer`; `grader_detail` vào `meta` API

### 2.7 error_handler — `nodes/error_handler.py`

CRITICAL error ở bất kỳ node nào → route về đây → viết `final_answer` fallback tiếng Việt → END.

---

## 3. Hai cổng độc lập (D2, D15)

Đây là thiết kế quan trọng nhất. **KHÔNG GỘP 2 cổng** vì sẽ tạo bug an toàn.

| Cổng | Guard | Vị trí | Ý nghĩa |
|------|-------|--------|---------|
| Cổng RETRIEVER | `needs_retrieval` | Sau planner (D2) | Có cần tìm kiếm external knowledge không? |
| Cổng GRADER | `required_outputs != []` | Sau synthesizer (D15) | Có tag contract nào cần kiểm tra không? |

**Ví dụ bug nếu gộp**: "đau ngực khi tập" → `needs_retrieval=false` (không cần tìm kiếm), nhưng `required_outputs=[red_flag_screen, referral_advice]`. Nếu gộp 2 cổng, grader bị skip → mất cảnh báo an toàn.

---

## 4. Cách PERSONA hoạt động trong flow

### 4.1 Persona được chọn từ đâu

```python
# configurable.persona_id → default "eca_default"
persona_id = config["configurable"].get("persona_id", "eca_default")
```

Frontend/client gửi `persona_id` trong request body → API truyền vào `configurable`.

### 4.2 Persona file format (MD)

```
# Tên Persona

## Identity
Name: <tên nhân vật> | Role: <vai trò> | Avatar: <file.png>

## Voice Identity
voice_path: "voices/<file>.wav"
language: vi|en

## Personality
Tone: <mô tả tone> | Formality: Formal|Semi-formal|Informal

## Behavioral Rules
- <rule 1>
- <rule 2>
- ...

## Response Formatting
- <formatting rule 1>
- <formatting rule 2>
```

### 4.3 Persona loading & caching

`_persona_loader.py`:
- Persona files được load từ `langgraph_agents/personas/{persona_id}.md`
- Parse bằng regex (theo `## Header` sections)
- Cache trong `_persona_cache` dict (in-memory)
- Fallback persona nếu file không tồn tại hoặc invalid

### 4.4 Persona prompt assembly

`build_persona_prompt(persona, mode)` tạo block:
```
You are <identity>

## Your Personality
<personality>

## Rules
<behavioral_rules>

## Formatting
<response_formatting>

## Turn hint
<mode-specific 1-line hint>
```

### 4.5 Persona chỉ ảnh hưởng synthesizer

Persona KHÔNG ảnh hưởng đến:
- **memory** — chỉ assemble context, không LLM
- **planner** — dùng system prompt riêng, không persona (planner không phải user-facing)
- **retriever_agent** — dùng system prompt riêng, persona không liên quan đến search strategy
- **grader** — LLM chấm từng mục kiểm, không regex

### 4.6 3 persona hiện có

| persona_id | Tên | Tone | Ngôn ngữ | File |
|---|---|---|---|---|
| `eca_default` | Seele | Warm, professional | EN | `eca_default.md` |
| `eca_clinical` | Dr. Hoài Anh | Authoritative, formal | VI | `eca_clinical.md` |
| `eca_friendly` | ECA Buddy | Casual, cheerful | VI | `eca_friendly.md` |

---

## 5. Data flow (state shape)

```python
class AgentState(TypedDict):
    messages:            list        # Annotated[add_messages] — carries all chat + tool results
    required_outputs:    list[str]   # Planner set — Grader reads
    resolved_query:      str         # Planner set — Retriever + Synthesizer reads
    needs_retrieval:     bool        # Planner set — Routing reads
    needs_clarification: bool        # Planner set — Routing + Synthesizer reads
    grader_result:       str         # Grader set — Routing reads ("pass"|"retry"|"pass_with_warning")
    grader_feedback:     str|None    # Grader set — các mục thiếu, Synthesizer đọc (viết thêm)
    retry_count:         int         # Grader set — Grader/Synthesizer reads (max 1)
    grader_detail:       dict        # Grader set — mục kiểm → ok|synth_missed|source_silent|no_evidence
    final_answer:        str         # Synthesizer/Grader/ErrorHandler set — API response
    total_tokens:        int         # Accumulated via operator.add
    retriever_rounds:    int         # Retriever set — Routing reads (hard cap 2)
    errors:              list[dict]  # Annotated[operator.add] — any node can append
```

---

## 6. Để thêm PERSONA mới cho character

### Bước 1: Tạo persona file MD

Tạo file `agenticRAG/langgraph_agents/personas/{persona_id}.md` theo format ở §4.2.

### Bước 2: Không cần sửa code

Hệ thống tự load từ `personas_dir` dựa trên `persona_id`. Chỉ cần file tồn tại là hoạt động.

### Bước 3: Frontend gửi persona_id

Frontend thêm `persona_id` vào request body → API truyền vào `configurable` → synthesizer đọc.

### Các thứ có thể cần sửa nếu muốn persona ảnh hưởng sâu hơn

| Muốn persona ảnh hưởng đến... | Sửa ở đâu |
|---|---|
| Tone câu an toàn / dòng nguồn | `tag_contract.py` — template mặc định; câu chữ persona trong `personas/*/*.md` |
| Cách planner phân loại intent | `planner.py` — `_PLANNER_SYSTEM_PROMPT` (hiếm khi cần) |
| Retriever search strategy | `retriever_agent.py` — `_RETRIEVER_PROMPT_BASE` (hiếm khi cần) |

---

## 7. File index

| File | Vai trò |
|---|---|
| `graph.py` | Định nghĩa StateGraph, 8 node, edges, routing |
| `state.py` | AgentState TypedDict |
| `routing.py` | `retrieval_fault`, `route_after_*`, `check_errors` |
| `evidence.py` | `EvidenceItem` dùng chung (synthesizer, grader, dòng nguồn) |
| `tag_contract.py` | `TAG_CONTRACT` + câu mở đầu/dòng kết/dòng nguồn do code phát |
| `nodes/memory.py` | Memory node |
| `nodes/planner.py` | Planner node + PlanOutput + TAG vocabulary |
| `nodes/retriever_agent.py` | Retriever agent + tool list + system prompt |
| `nodes/kimodo.py` | Kimodo motion generation node |
| `nodes/synthesizer.py` | **Synthesizer — nơi persona được inject** |
| `nodes/grader.py` | LLM chấm (`_judge`) + `_finish` nối dòng kết; TAG_RULES + 8 hàm `_has_*` (công cụ đo) |
| `nodes/error_handler.py` | Error handler node |
| `nodes/_persona_loader.py` | Load/cache persona MD, build prompt |
| `personas/*.md` | 3 persona definitions |
