---
title: "Plan: Thông báo đổi nhân vật (avatar change) cho LLM"
status: approved
branch: feat/user-preferences
author: K (Senior Solution Architect)
date: 2026-09-09
implementer: N (Senior Developer)
effort: ~40 phút (BE ~20, FE ~20)
tags: [plan, avatar, persona, synthesizer, ephemeral, langgraph]
related:
  - "[[agenticRAG/langgraph_agents/nodes/synthesizer]]"
  - "[[agenticRAG/langgraph_agents/nodes/_persona_loader]]"
  - "[[agenticRAG/langgraph_agents/api/schemas]]"
  - "[[agenticRAG/langgraph_agents/api/main]]"
  - "[[ECA_UI/frontend/src/contexts/MotionContext]]"
  - "[[ECA_UI/frontend/src/contexts/ChatContext]]"
  - "[[ECA_UI/frontend/src/lib/api]]"
  - "[[docs/plans/preferences-v3-plan]]"
  - "[[docs/architecture/langgraph-flow-persona]]"
---

# Plan — Thông báo đổi nhân vật (avatar change) cho LLM

> **Nguồn:** draft của N (09-09-2026), K review và sửa. Mục tiêu và ràng buộc
> ephemeral của draft giữ nguyên; cơ chế inject bị thay vì không chạy được —
> xem §"Ba lỗi chặn trong plan gốc".

## Context

Khi user đổi VRM (`bronya` → `anne`) rồi mới chat, backend hiện
không biết gì về việc đổi: `/chat` chỉ mang `persona_id` của avatar **hiện tại**,
không có dấu vết của avatar trước đó. Mục tiêu là để LLM ack tự nhiên đúng **một
turn**, không tạo request/row/event riêng, và spam đổi chỉ tính delta cuối.

Plan gốc đúng về mục tiêu và đúng về ràng buộc ephemeral, nhưng **cơ chế inject
không chạy được**. Ba phát hiện dưới đây thay đổi thiết kế; phần còn lại của plan
gốc giữ nguyên.

---

## Ba lỗi chặn trong plan gốc

### B1 — §4.3 là no-op: SystemMessage bị lọc bỏ trước khi tới LLM

Plan inject `SystemMessage` vào `state["messages"]` từ `memory_node`. Nhưng
[synthesizer.py:370-377](agenticRAG/langgraph_agents/nodes/synthesizer.py#L370-L377)
lọc `state["messages"]` xuống chỉ còn Human + AIMessage-không-tool-call, và
comment nói thẳng ý đồ:

```python
# Include prior conversation (loaded by memory node into state messages)
# so the model has context for follow-ups ("what did I just say"). Keep
# plain user/assistant turns only — drop the memory SystemMessage, tool-call
# AIMessages, and ToolMessages (tool evidence is already in the system prompt).
history = [
    m for m in state.get("messages", [])
    if isinstance(m, HumanMessage)
    or (isinstance(m, AIMessage) and not getattr(m, "tool_calls", None))
]
```

`SystemMessage` inject vào đây **không bao giờ tới LLM**. Nguy hiểm hơn: unit test
ở §6 của plan gốc (kiểm tra `state.messages` chứa SystemMessage) sẽ **PASS** trong
khi feature hoàn toàn không chạy E2E.

→ **Fix**: bỏ hẳn thay đổi ở `memory.py`. Truyền qua `config` thẳng tới
`synthesizer`, nơi đã đọc `config["configurable"]` sẵn.

### B2 — `to` là thừa và có thể desync với `persona_id`

[ChatContext.tsx:479](ECA_UI/frontend/src/contexts/ChatContext.tsx#L479) đã gửi
`personaId: selectedVrmId || undefined` — tức `persona_id` **chính là** avatar
hiện tại. Contract `"bronya->anne"` nhét `to` lần thứ hai vào request, tạo hai
nguồn sự thật có thể mâu thuẫn (client bug → `persona_id=anne` nhưng
`avatar_change=...->miki`).

→ **Fix**: field chỉ mang avatar **trước**. `to` đọc từ `persona_id`.
Điều này cũng xoá luôn lỗi số học `max_length=65` (32+2+32 = **66**, không phải
65) và sự lệch chuẩn `{1,32}` vs `{1,64}` mà `persona_id` dùng ở
[schemas.py:10](agenticRAG/langgraph_agents/api/schemas.py#L10).

### B3 — LLM biết display name, không biết slug

Prompt nói `You are Bronya.` — display name viết hoa, resolve bởi
`persona_name()` ([_persona_loader.py:429](agenticRAG/langgraph_agents/nodes/_persona_loader.py#L429)).
Inject slug thô sẽ cho LLM chuỗi nó chưa từng thấy: với `hatsune-miku`, model rất
dễ nhả nguyên văn *"bạn vừa đổi từ hatsune-miku"*.

→ **Fix**: resolve slug → display name bằng `get_persona()` + `persona_name()`.
**Bắt buộc bọc try/except**: `get_persona` **raise `PersonaError`** cho slug lạ
hoặc character đã deactivate ([_persona_loader.py:272-273](agenticRAG/langgraph_agents/nodes/_persona_loader.py#L272-L273))
— không bọc thì một avatar bị gỡ khỏi catalog sẽ làm **500 cả turn**.
Idiom fallback có sẵn ở [grader.py:286-291](agenticRAG/langgraph_agents/nodes/grader.py#L286-L291).

---

## Điểm plan gốc đã đúng (giữ nguyên, không cần code)

Ràng buộc "không persist" là **miễn phí** — ba rào chắn cấu trúc đã có sẵn:

1. **Không có checkpointer.** [graph.py:237](agenticRAG/langgraph_agents/graph.py#L237)
   là `g.compile()` trần; grep toàn repo `checkpointer|thread_id|MemorySaver` → 0 hit.
   State chết sau mỗi `astream`.
2. **`write_session_turn` nhận string, không nhận state**
   ([session_store.py:333-381](agenticRAG/langgraph_agents/db/session_store.py#L333-L381)) —
   ghi đúng 2 row với role hard-code `"user"`/`"assistant"`.
3. **STM shape `{q, a, ts}`** ([session_store.py:385](agenticRAG/langgraph_agents/db/session_store.py#L385))
   không có slot cho role khác.

→ Không cần đụng `session_store.py`, `stm.py`, không lo `_STM_TOKEN_BUDGET`.
Vì `memory.py` không đổi (B1), toàn bộ mục 4.3 của plan gốc **biến mất**.

---

## Thiết kế đã sửa

### Contract

```jsonc
POST /chat
{
  "query": "bài tập lưng?",
  "persona_id": "anne",              // avatar HIỆN TẠI (đã có sẵn)
  "previous_persona_id": "bronya"    // MỚI — avatar trước, null nếu không đổi
}
```

`null` cho ~99% request. Cùng pattern với `persona_id` → không có luật xác thực
thứ hai phải bảo trì.

### Flow

```
FE  MotionContext.setSelectedVrmIdWrapped(miki)
      prevRef.current ??= selectedVrmId      // ghi 1 lần → collapse spam
    → user Send
    → POST /chat { persona_id: "miki", previous_persona_id: "bronya" }
    → prevRef.current = null                 // consumed
BE  main.py    → config.configurable.previous_persona_id
    → synthesizer resolve "bronya" → "Bronya", "miki" → "Miki"
    → append block vào voice card (LAST message, recency cao nhất)
    → write_session_turn chỉ ghi query gốc + final answer
```

`prevRef.current ??=` (chỉ ghi khi đang null) là mấu chốt collapse: đổi
`bronya→anne→miku→anne` giữ `"bronya"`, và nếu quay về đúng avatar cũ thì
`previous == persona_id` → backend bỏ qua.

---

## Thay đổi chi tiết — 6 file

### 1. `agenticRAG/langgraph_agents/api/schemas.py` — thêm 1 field vào `ChatRequest`

```py
previous_persona_id: Optional[str] = Field(
    default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$"
)
```

Đặt cạnh `persona_id` (dòng 10). **Không đụng `SyncedPrefs`** — nó có
`extra="forbid"` và là whitelist PHI cho `users.preferences`; field này không
persist nên không thuộc về đó. Không cần validator `from != to` (xử lý ở
synthesizer, xem dưới).

### 2. `agenticRAG/langgraph_agents/api/main.py:280-293` — thêm 1 key vào config

```py
"previous_persona_id": req.previous_persona_id,
```

Thêm vào dict `config["configurable"]` hiện có (đang 9 key). `config` được truyền
nguyên vẹn xuống `graph.astream` ở [main.py:493](agenticRAG/langgraph_agents/api/main.py#L493) — không cần đổi gì khác.

### 3. `agenticRAG/langgraph_agents/nodes/synthesizer.py` — chỗ duy nhất có logic

Sau `persona = get_persona(persona_id, locale)` ([dòng 323](agenticRAG/langgraph_agents/nodes/synthesizer.py#L323)),
dựng một block ngắn; thêm helper cạnh các `_build_*` hiện có:

```py
def _build_avatar_switch_note(prev_id, persona_id, persona, locale) -> str:
    """Một dòng ngữ cảnh cho turn ngay sau khi user đổi avatar.

    Đọc từ config chứ không từ state.messages: synthesizer lọc bỏ mọi
    SystemMessage khỏi history (xem `history` bên dưới), nên context
    ephemeral phải đi đường config.
    """
    if not prev_id or prev_id == persona_id:
        return ""
    try:
        prev_name = persona_name(get_persona(prev_id, locale))
    except PersonaError:
        return ""           # character đã gỡ khỏi catalog — im lặng bỏ qua
    return (
        f"\n\n## Avatar switch\n"
        f"The user just switched from {prev_name} to you. Acknowledge it "
        f"briefly ONLY if it fits naturally; otherwise ignore it and answer "
        f"the question."
    )
```

Nối vào **voice card**, không nối vào `system`:

```py
voice_card = build_voice_card(persona, mode) + _build_avatar_switch_note(...)
msgs = [SystemMessage(content=system), *history,
        HumanMessage(content=resolved_query),
        SystemMessage(content=voice_card)]
```

Hai lý do voice card thay vì `system`:
- **Recency** — `build_voice_card` đặt cuối chính vì lý do này, docstring
  [_persona_loader.py:475-490](agenticRAG/langgraph_agents/nodes/_persona_loader.py#L475-L490)
  giải thích: cái gần điểm generate nhất là cái model bắt chước.
- **Prompt cache** — `system` là prefix dài được cache; chèn text đổi-mỗi-turn
  vào đó sẽ invalidate cache. Voice card ở cuối nên vô hại.

Import thêm: `persona_name`, `PersonaError` (dòng 33 đã import từ cùng module).

### 4. `ECA_UI/frontend/src/lib/api.ts` — **file plan gốc bỏ sót**

`StreamChatOptions` ([dòng 161](ECA_UI/frontend/src/lib/api.ts#L161)) + body
`JSON.stringify` ([dòng 199](ECA_UI/frontend/src/lib/api.ts#L199)):

```ts
previousPersonaId?: string      // vào interface
previous_persona_id: previousPersonaId ?? null,   // vào body (camel→snake)
```

### 5. `ECA_UI/frontend/src/contexts/MotionContext.tsx` — owner của prevRef

Plan gốc ghi `src/context/AvatarContext` hoặc `VrmViewer.tsx` — **cả hai đều
không tồn tại**. Owner thật của `selectedVrmId` là `MotionContext`
([dòng 54](ECA_UI/frontend/src/contexts/MotionContext.tsx#L54)), handler là
`setSelectedVrmIdWrapped` ([dòng 367](ECA_UI/frontend/src/contexts/MotionContext.tsx#L367)).

- Thêm `const prevAvatarRef = useRef<string | null>(null)`.
- Đầu `setSelectedVrmIdWrapped`, sau guard `if (switchingId) return`:
  `if (id !== selectedVrmId) prevAvatarRef.current ??= selectedVrmId`
- **Nhánh catch fallback-về-`anne`** ([~dòng 398](ECA_UI/frontend/src/contexts/MotionContext.tsx#L398)):
  fetch lỗi → quay về `anne`. Không xử lý thì FE báo một lần đổi **đã bị revert**.
  Trong catch: nếu fallback về đúng `prevAvatarRef.current` thì reset ref về
  `null`; nếu không thì để nguyên (`bronya → anne` vẫn là delta thật).
- Expose qua context value: `consumePreviousAvatar()` — trả ref rồi set `null`.

### 6. `ECA_UI/frontend/src/contexts/ChatContext.tsx:469-484` — call site

```ts
previousPersonaId: consumePreviousAvatar(),
```

Gọi **trong** `streamChat({...})` args. Consume tại điểm gửi, không phải điểm đổi.

**Không đụng**: `session_store.py`, `stm.py`, `memory.py`, `planner`, `grader`,
`kimodo`, DB schema, `SyncedPrefs`, `preferences.ts`.

---

## Edge cases

| Case | Kết quả |
| --- | --- |
| Spam `bronya→anne→miku→anne` | `??=` giữ `"bronya"`; nhưng `anne == persona_id`? Không — cuối là `anne`, `prev=bronya` → inject đúng 1 lần |
| Đổi rồi quay về chính nó | `previous == persona_id` → `_build_avatar_switch_note` trả `""` |
| Reload trước khi chat | Mất pending (**quyết định: chấp nhận**) |
| Slug sai pattern | `422` |
| Character đã deactivate | `PersonaError` → bỏ qua im lặng, **không 500** |
| Không gửi field | `None` → nhánh chết ngay ở dòng đầu helper |
| Guest (no idToken) | Không ảnh hưởng — field không liên quan prefs |

---

## Test

Test §6 của plan gốc cần sửa — hai trong ba test là vô nghĩa:

- ~~unit `memory_node` chứa SystemMessage~~ → **xoá**. Test này PASS trong khi
  feature chết (B1). Thay bằng: **unit `_build_avatar_switch_note`** — 4 case:
  happy path trả display name viết hoa; `prev == current` → `""`; `prev=None` →
  `""`; slug không tồn tại (monkeypatch `get_persona` raise `PersonaError`) → `""`.
- ~~integration: `load_session_messages` không chứa dòng avatar~~ → gần như vô
  nghĩa, đã được bảo đảm bởi 3 rào chắn cấu trúc. Giữ lại như **regression guard
  rẻ tiền** cũng được, nhưng nó không phải test chính.
- **Test quan trọng nhất (thiếu hoàn toàn trong plan gốc)**: assert chuỗi
  `"just switched from Bronya"` **thực sự có trong `msgs` gửi cho LLM**. Patch
  `get_chat_model` trong `synthesizer`, chạy `synthesizer_node` với
  `config.configurable.previous_persona_id="bronya"`, inspect message cuối. Đây
  là test duy nhất bắt được lỗi B1.
- E2E: click 3 avatar → Send → log prompt chứa block, turn kế tiếp **không** chứa.

---

## Verification

```bash
# BE
cd "d:/Swin documents/Virtual-Verbal-Assistant"
pytest -m unit                                    # không regression
pytest agenticRAG/tests -k "synthesizer or persona" -v

# Manual QA — cần Redis local + VVA_PG_DSN
# 1. Chọn bronya, đổi sang anne, đổi sang miki, đổi lại anne
# 2. Hỏi: "mày nhớ tao vừa đổi gì không?"  → phải nhắc Bronya
# 3. Hỏi tiếp câu nữa                        → KHÔNG được nhắc lại
# 4. DevTools Network: đúng 1 request /chat, không request riêng cho đổi avatar

# FE
cd ECA_UI/frontend && npm run build            # tsc -b, KHÔNG dùng tsc --noEmit
```

## Tiêu chí done

- Đổi avatar không tạo request riêng, `messages`/`stm` không tăng size.
- Turn kế LLM nhắc đúng **display name** (`Bronya`, không phải `bronya`).
- Turn sau nữa tự quên.
- Character deactivate không làm 500.
- `pytest -m unit` xanh; `npm run build` xanh.

## Rollout

1. BE ~20 phút: `schemas.py` + `main.py` + `synthesizer.py` (+ helper & test).
2. FE ~20 phút: `api.ts` + `MotionContext.tsx` + `ChatContext.tsx`.
3. Manual QA theo kịch bản trên.

Rollback: revert 6 file. Client cũ vẫn chạy — field optional, default `None`.

---

## Ghi chú kiến trúc (ngoài scope, để cân nhắc sau)

Có một lựa chọn thay thế bền hơn: ghi `persona_id` vào cột `extras` JSONB sẵn có
của bảng `messages` ([session_store.py:373](agenticRAG/langgraph_agents/db/session_store.py#L373)),
rồi so persona của turn trước với turn này. Không cần field request nào, sống qua
reload và cross-device. Giá phải trả: đổi shape STM `{q,a,ts}` (hiện drop
`extras`) → đụng `memory.py` + `stm.py`. Đắt hơn nhiều cho một feature ephemeral,
nên **không khuyến nghị bây giờ** — nhưng nếu sau này cần "avatar nào đã nói câu
nào" cho lý do khác, nó là đường đi đúng.

Riêng biệt: `selected_character_slug` trong prefs là **starred default**, không
phải live selection — `setSelectedVrmIdWrapped` không bao giờ gọi `patch()`. Đổi
avatar rồi reload sẽ mất lựa chọn trừ khi user bấm sao. Đó là một gap thật của
branch `feat/user-preferences`, nhưng độc lập với feature này.
