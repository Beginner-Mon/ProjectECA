---
tags: [plan, agent, frontend]
status: done
date: 2026-10-09
---

# Nhãn trạng thái đúng thứ tự + Anne biết mình đang nói — Implementation Plan

> **Dành cho agent thực thi.** Làm lần lượt từng task, từng bước, theo đúng thứ tự.
> Không thiết kế lại, không gộp task, không làm việc ngoài danh sách. Mỗi bước có ô
> `- [ ]` để đánh dấu. Gặp điều kế hoạch không lường hoặc một bước không làm được
> như mô tả: dừng, ghi vào worklog, báo Tri.

**Mục tiêu:** (1) Dòng trạng thái trên giao diện đi đúng thứ tự "đợi → đang tra →
sắp xong → câu trả lời", không còn nhảy ngược. (2) Khi lượt có giọng nói, Anne được
báo là mình đang nói thành tiếng.

**Vì sao (đọc từ code, 09/10/2026):**

- `ChatContext.tsx` có một đồng hồ 2,5 giây: hết giờ mà backend chưa báo gì thì tự
  đổi "Đợi mình một chút…" thành "Đang soạn cho bạn…". Planner và retriever thường
  lâu hơn 2,5 giây, nên "đang soạn" hiện trước rồi mới tới "đang tìm trong thư viện".
- Tin `stage {node: synthesizer, status: started}` được gửi cùng lúc với chữ đầu
  tiên, nên nhãn cuối bị xóa ngay, không ai kịp thấy.
- Nhãn tra cứu chỉ lấy nguồn đầu tiên (`sources[0]`).
- `output_mode` (bật giọng nói) đã đi từ frontend xuống `config` của lượt, nhưng
  không node nào đọc; Anne không biết lượt đó mình đang nói.

**Cách làm:** không thêm khóa, không thêm đồng hồ, không thêm lời gọi LLM.

| Thay đổi | Ở đâu |
| --- | --- |
| Gửi tin "synthesizer bắt đầu" ngay khi node synthesizer chạy | backend |
| Hiện đủ các nguồn tra cứu; không nguồn nào thì giữ nhãn hiện tại | frontend |
| Xóa đồng hồ 2,5 giây | frontend |
| "Đang soạn cho bạn…" → "Sắp xong rồi…"; "Writing this up for you..." → "Almost there..." | persona Anne + chuỗi mặc định frontend |
| Một câu cho Anne khi lượt có giọng nói | backend |

Thứ tự nhãn sau khi sửa: "Đợi mình một chút…" → nhãn nguồn (chỉ ở lượt có tra) →
"Sắp xong rồi…" → chữ. Số đo 09/10 trên 6 lượt thật: mỗi nhãn kéo dài ít nhất 1,1
giây, trừ lượt có câu cảnh báo do code phát (chữ hiện ngay), nên không cần thời gian
hiển thị tối thiểu.

**Tech stack:** Python 3, LangGraph 1.1.6, FastAPI SSE; Vite + React + TypeScript,
vitest. Không thêm thư viện.

## Ràng buộc chung

- Nhánh: `feature/stage-labels`, tạo từ `origin/feature/langgraph-rewrite`. Không
  làm trên `release`. Không `git push origin HEAD:release`. Không force-push.
- Mỗi task một commit; sau mỗi commit chạy `git push origin feature/stage-labels`.
- Không ghi vào Neon. **Không chạy `scripts/sync_personas_to_db.py`** (việc đó Tri
  làm lúc ship).
- Trong `agenticRAG/langgraph_agents/personas/**` chỉ được sửa đúng hai dòng nêu ở
  Task 4. Không add, commit hay stash `.claude/CLAUDE.md`.
- Không in ra DSN, mật khẩu, API key hay nội dung `agenticRAG/.env`.
- Không thêm `setTimeout` hay bất kỳ thời gian chờ viết cứng nào để đổi nhãn.
- Câu chữ trong kế hoạch này chép nguyên văn.
- Ghi việc đã làm vào `docs/worklogs/DD-MM-YYYY.md` (ngày thực thi); không sửa mục
  của người khác.

| Việc | Lệnh (PowerShell, chạy ở gốc repo) |
| --- | --- |
| Python | `C:\Miniconda\envs\firstconda\python.exe` (không dùng `python` trần) |
| Toàn bộ test backend | `& C:\Miniconda\envs\firstconda\python.exe -m pytest tests/langgraph_agents -m "not integration and not e2e" -W ignore::PendingDeprecationWarning -q` |
| Một file test | `& C:\Miniconda\envs\firstconda\python.exe -m pytest <đường dẫn> -W ignore::PendingDeprecationWarning -q` |
| Test frontend | trong `ECA_UI/frontend`: `npm run build`; `npx vitest run`. Không chạy `npm install`. |

Fail được phép trong toàn bộ test backend:
`test_phase2_5_integration.py::TestGraderRules::test_has_danger_warning_present` và
`test_phase6_circuit_breaker.py::test_planner_degrades_when_no_provider_can_be_built`.

Viết tắt: `LA/` = `agenticRAG/langgraph_agents/`, `T/` = `tests/langgraph_agents/`,
`FE/` = `ECA_UI/frontend/src/`.

---

## Task 0 — Nhánh

- [ ] **Bước 1.** `git fetch origin`, rồi
      `git checkout -b feature/stage-labels origin/feature/langgraph-rewrite`.
- [ ] **Bước 2.** Chạy toàn bộ test backend và `npx vitest run`. Ghi số pass/fail
      vào worklog làm mốc.

## Task 1 — Anne biết mình đang nói

**File:** `LA/api/main.py`, `LA/nodes/synthesizer.py`, `T/test_synthesizer_blocks.py`,
`T/test_phase5_sse.py`.

- [ ] **Bước 1. Viết test đỏ** trong `T/test_synthesizer_blocks.py` (cạnh các test
      `_build_body_state_note` sẵn có, dùng lại `_motion_tm`):

    | Test | Dựng | Khẳng định |
    | --- | --- | --- |
    | `test_voice_line_alone` | `_build_body_state_note([], speaks_aloud=True)` | kết quả chứa `## Your body this turn` và `You are speaking this reply aloud in your own voice; the user hears you.` |
    | `test_voice_line_with_motion` | motion `{"state": "queued", "prompt": "squat"}`, `speaks_aloud=True` | đúng một tiêu đề `## Your body this turn`; có cả câu `You are about to show "squat"` lẫn câu giọng nói |
    | `test_no_voice_line_by_default` | `_build_body_state_note([])` và `_build_body_state_note([], speaks_aloud=False)` | cả hai `== ""` |
    | `test_motion_only_output_unchanged` | motion `unavailable`, không truyền `speaks_aloud` | kết quả không chứa `speaking this reply aloud` |

- [ ] **Bước 2. Viết test đỏ** trong `T/test_phase5_sse.py`: một `astream` giả ghi
      lại `config` nhận được.

    | Test | Dựng | Khẳng định |
    | --- | --- | --- |
    | `test_config_speaks_aloud_when_voice_on` | POST `/chat` với `output_mode: "both"`, `tts_enabled` patch trả `True` | `config["configurable"]["speaks_aloud"] is True` |
    | `test_config_not_speaking_when_tts_off` | `output_mode: "both"`, `tts_enabled` trả `False` | `speaks_aloud is False` |
    | `test_config_not_speaking_in_text_mode` | `output_mode: "text"` | `speaks_aloud is False` |

- [ ] **Bước 3.** Chạy hai file test. Kỳ vọng: các test mới FAIL.
- [ ] **Bước 4. Sửa `LA/api/main.py`:** trong dict `config = {"configurable": {...}}`
      của route `/chat`, thêm ngay dưới `"output_mode": req.output_mode,`:

    ```python
            # Lượt này câu trả lời có được đọc thành tiếng không. Synthesizer
            # đọc cờ này để báo cho nhân vật (không báo cơ chế).
            "speaks_aloud": req.output_mode in ("speech", "both") and tts_enabled(),
    ```

- [ ] **Bước 5. Sửa `LA/nodes/synthesizer.py`:**
  - Đổi tên hàm `_build_body_state_note` hiện có thành `_motion_line`, chữ ký
    `_motion_line(messages: list) -> str`. Trong hai lệnh `return` có nội dung, bỏ
    tiền tố `"\n\n## Your body this turn\n"`, chỉ trả câu. Các `return ""` giữ nguyên.
  - Thêm ngay dưới:

    ```python
    _VOICE_LINE = "You are speaking this reply aloud in your own voice; the user hears you."


    def _build_body_state_note(messages: list, speaks_aloud: bool = False) -> str:
        """Thân thể của nhân vật ở lượt này: cử động (nếu có) và giọng nói (nếu bật).

        Rỗng khi không có gì để nói; khối không có dữ liệu không vào prompt.
        Không nhắc cơ chế.
        """
        lines = [line for line in (_motion_line(messages),
                                   _VOICE_LINE if speaks_aloud else "") if line]
        if not lines:
            return ""
        return "\n\n## Your body this turn\n" + "\n".join(lines)
    ```

  - Trong `synthesizer_node`, đổi lời gọi thành:

    ```python
    body_note = _build_body_state_note(
        state.get("messages", []),
        speaks_aloud=bool(config["configurable"].get("speaks_aloud")),
    )
    ```

- [ ] **Bước 6.** Chạy `T/test_synthesizer_blocks.py`, `T/test_phase5_sse.py`. Kỳ
      vọng: PASS, kể cả các test `_build_body_state_note` cũ.
- [ ] **Bước 7.** Chạy toàn bộ test backend. Commit
      `feat(stage-labels T1): tell the character when this reply is spoken aloud`. Push.

## Task 2 — Backend: báo "synthesizer bắt đầu" đúng lúc

**File:** `LA/nodes/synthesizer.py`, `LA/api/main.py`, `T/test_phase5_sse.py`,
`T/test_code_written_lines.py`.

- [ ] **Bước 1. Viết test đỏ:**

    | Test | File | Dựng | Khẳng định |
    | --- | --- | --- | --- |
    | `test_stage_started_sent_before_tools_finish` | `T/test_phase5_sse.py` | `astream` giả phát lần lượt: `("updates", {"planner": {}})`, `("custom", {"stage": "synthesizer_started"})`, `("custom", {"content": "A"})`, `("updates", {"synthesizer": {"final_answer": "A"}})` | có đúng một sự kiện `stage` với `node == "synthesizer"`, `status == "started"`, đứng trước sự kiện `token` đầu tiên; payload `stage` không sinh ra sự kiện `token` nào; nối các `token` == `"A"` |
    | `test_stage_started_only_once` | `T/test_phase5_sse.py` | hai payload `{"stage": "synthesizer_started"}` rồi token | vẫn đúng một sự kiện `started` |
    | `test_synthesizer_announces_start_first` | `T/test_code_written_lines.py` | chạy `synthesizer_node` với `writer` giả, `retry_count=0` | payload đầu tiên của writer == `{"stage": "synthesizer_started"}`; nó đứng trước mọi payload có `content` |
    | `test_no_start_announcement_on_second_pass` | `T/test_code_written_lines.py` | `retry_count=1`, có `grader_feedback` và `final_answer` | không payload nào có khóa `stage` |

    `test_sse_chat_stage_started_before_tokens` sẵn có phải vẫn PASS (đường dự phòng
    khi node không báo).
- [ ] **Bước 2.** Chạy. Kỳ vọng: các test mới FAIL.
- [ ] **Bước 3. Sửa `LA/nodes/synthesizer.py`:** chuyển khối lấy `writer` lên đầu
      `synthesizer_node` (ngay sau khi tính `is_rewrite`) và báo bắt đầu ở đó; xóa
      khối `try: writer = get_stream_writer()` cũ ở phía dưới.

    ```python
    try:
        writer = get_stream_writer()
    except RuntimeError:
        writer = None
    if writer is not None and state.get("retry_count", 0) == 0:
        # Báo cho giao diện là mọi việc tra cứu đã xong và sắp có câu trả lời.
        # Gửi ngay lúc node chạy, không đợi chữ đầu tiên của LLM.
        writer({"stage": "synthesizer_started"})
        await asyncio.sleep(0)
    ```

- [ ] **Bước 4. Sửa `_stream_chat` trong `LA/api/main.py`:** trong nhánh
      `elif mode == "custom":`, thêm một nhánh đứng trước nhánh `"content" in payload`:

    ```python
            elif isinstance(payload, dict) and payload.get("stage") == "synthesizer_started":
                if not conversation_stage_started:
                    yield encode_event(
                        "stage",
                        {"node": "synthesizer", "status": "started"},
                    )
                    conversation_stage_started = True
    ```

    Nhánh `"content"` giữ nguyên (đường dự phòng: vẫn báo `started` nếu chưa báo).
- [ ] **Bước 5.** Test nào đang khẳng định "payload đầu tiên của writer là câu mở
      đầu" thì sửa thành "payload đầu tiên có khóa `content`". Ghi tên test vào worklog.
- [ ] **Bước 6.** Chạy `T/test_phase5_sse.py`, `T/test_code_written_lines.py`,
      `T/test_addition_pass.py`. Kỳ vọng: PASS.
- [ ] **Bước 7.** Chạy toàn bộ test backend. Commit
      `fix(stage-labels T2): announce the answer phase when the synthesizer starts, not at the first token`. Push.

## Task 3 — Frontend: nhãn theo tin thật, bỏ đồng hồ

**File:** `FE/lib/stageLabel.ts`, `FE/lib/stageLabel.test.ts`,
`FE/contexts/ChatContext.tsx`, `FE/lib/characterCopy.ts`.

- [ ] **Bước 1. Sửa test** `FE/lib/stageLabel.test.ts` (đỏ trước):
  - Thêm: `sources: ['library', 'memory']` →
    `` `${vi.stage_searching} ${vi.stage_recalling}` ``.
  - Thêm: `sources: ['memory', 'library']` → nhãn theo đúng thứ tự đó.
  - Thêm: `sources: ['library', 'unknown-source']` → chỉ `vi.stage_searching`.
  - Tách test "retriever không gọi gì, hoặc synthesizer started, ra composing" thành
    hai: (a) retriever `complete` với `sources: []` hoặc không có `sources` → trả
    `current` (thử với `current = vi.stage_thinking`); (b) synthesizer `started` →
    `vi.stage_composing`.
- [ ] **Bước 2.** `npx vitest run src/lib/stageLabel.test.ts`. Kỳ vọng: FAIL.
- [ ] **Bước 3. Sửa `FE/lib/stageLabel.ts`:** thay nhánh `retriever_agent` bằng:

    ```ts
      // retriever_agent complete: nhãn của mọi nguồn được tra, theo thứ tự gọi.
      // Các tool chạy song song nên các nhãn hiện cùng lúc.
      if (event.node === 'retriever_agent' && event.status === 'complete') {
        const labels = (event.sources ?? [])
          .map((source) => SOURCE_TO_KEY[source])
          .filter((key): key is StageCopyKey => Boolean(key))
          .map((key) => copy[key])
        // Không tra nguồn nào: giữ nhãn hiện tại. Nhãn cuối chỉ đổi khi backend
        // báo synthesizer bắt đầu.
        return labels.length > 0 ? labels.join(' ') : current
      }
    ```

    Sửa chú thích đầu file cho đúng hành vi mới.
- [ ] **Bước 4. Sửa `FE/contexts/ChatContext.tsx`:**
  - Xóa khối đồng hồ dự phòng (chú thích "Fallback: nếu backend không emit
    retriever…" và lệnh `setTimeout(…, 2500)`). Dòng
    `setStageLabel(stageLabelFor(null, uiRef.current, null))` giữ nguyên.
  - Xóa `stageTimeoutRef` và mọi dòng `if (stageTimeoutRef.current) clearTimeout(...)`
    (khai báo `useRef` và các chỗ dùng trong reset, stop, lỗi, xử lý `stage`,
    `token`, `motion`). Sau bước này file không còn chữ `stageTimeoutRef`.
  - Sửa chú thích "Giữ COMPOSING tới token đầu để che TTFT" thành: nhãn cuối hiện từ
    lúc backend báo synthesizer bắt đầu tới chữ đầu tiên.
- [ ] **Bước 5. Sửa `FE/lib/characterCopy.ts`** (chuỗi mặc định):
  - en: `stage_composing: 'Almost there...'`
  - vi: `stage_composing: 'Sắp xong rồi...'`
- [ ] **Bước 6.** Trong `ECA_UI/frontend`: `npx vitest run` và `npm run build`. Kỳ
      vọng: cả hai xanh. Test nào khẳng định chuỗi mặc định cũ thì sửa theo chuỗi mới.
- [ ] **Bước 7.** Commit
      `fix(stage-labels T3): labels follow backend events, all sources shown, no timer`. Push.

## Task 4 — Câu chữ của Anne

**File:** `LA/personas/anne/en.md`, `LA/personas/anne/vi.md`. Chỉ sửa đúng hai dòng.

- [ ] **Bước 1.** `anne/vi.md`: `stage_composing: "Đang soạn cho bạn..."` →
      `stage_composing: "Sắp xong rồi..."`.
- [ ] **Bước 2.** `anne/en.md`: `stage_composing: "Writing this up for you..."` →
      `stage_composing: "Almost there..."`.
- [ ] **Bước 3.** Chạy toàn bộ test backend. Commit
      `feat(stage-labels T4): Anne's last status line says "almost there"`. Push.

Không sync vào DB. Bronya, Miku, Miki giữ nguyên.

## Task 5 — Tài liệu

- [ ] **Bước 1.** `docs/architecture/langgraph-flow-persona.md`: nếu có đoạn mô tả
      sự kiện `stage` hoặc nhãn trạng thái thì cập nhật theo hành vi mới; thêm một
      dòng về câu giọng nói trong khối "Your body this turn".
- [ ] **Bước 2.** `docs/tracking/tech-debt.md`, mục "Agent context": thêm
  - Câu `stage_composing` của Bronya, Miku, Miki còn nói "soạn/viết"; đổi khi chuyển
    đổi ba nhân vật đó.
  - Kiến thức về hệ thống cho nhân vật: viết tài liệu nhìn từ phía người dùng và
    một tool đọc tài liệu, chạy in-process. Tri quyết để sau (09/10). Không dùng
    bảng năng lực viết tay.
  - Ở lượt có tra cứu, model mất 3–7 giây mới ra chữ đầu tiên (đo 09/10, 6 lượt
    local); chưa tách nguyên nhân là suy nghĩ ngầm hay prompt dài.
  - Nhãn nhiều nguồn được nối thành một dòng; bong bóng trên đầu nhân vật
    (`ThinkingBubble`, `whitespace-nowrap`) có thể dài trên màn hình hẹp.
- [ ] **Bước 3.** Chép kế hoạch này vào `docs/plans/stage-labels-voice.md` với YAML
      frontmatter (`tags: [plan, agent, frontend]`, `status: done`, `date`).
- [ ] **Bước 4.** Worklog: mục báo cáo theo mẫu ở cuối. Commit
      `docs(stage-labels T5): flow notes, tech debt, worklog`. Push.

---

## Kiểm chứng

**Test tự động:** toàn bộ test backend (chỉ hai fail được phép), `npm run build`,
`npx vitest run`.

**Thử tay trên UI local — do Tri làm.** Backend chạy với `VVA_PERSONA_SOURCE=files`
(để đọc câu mới của Anne từ file, chưa cần sync DB).

| Câu thử | Kỳ vọng |
| --- | --- |
| "xin chào" | "Đợi mình một chút…" → "Sắp xong rồi…" → chữ. Không có nhãn thư viện. |
| "bài tập cho đau lưng dưới" | "Đợi mình một chút…" → "Đang tìm trong thư viện…" → "Sắp xong rồi…" → chữ. "Sắp xong rồi…" không bao giờ đứng trước nhãn thư viện. |
| "hôm trước mình hỏi gì về lưng nhỉ" | nhãn "Để mình nhớ lại…" có hiện |
| "mình bị đau ngực khi tập" | câu cảnh báo hiện ngay; không nhãn nào nhảy qua lại |
| Bật giọng nói, hỏi "bạn đang nói với mình đấy à?" | Anne nhận là mình đang nói; không nhắc tên công nghệ |
| Tắt giọng nói, hỏi lại câu trên | Anne không nói là người dùng đang nghe thấy giọng mình |

Bộ đo `run_context_probe.py` không cần chạy lại: bộ đo chạy ở chế độ chữ nên prompt
của synthesizer không đổi, và kế hoạch này không thêm lời gọi LLM nào.

## Việc của Tri khi ship (agent không làm)

Câu `stage_composing` của Anne nằm trong DB ở prod. Ngay trước `git push origin
release` phải chạy `scripts/sync_personas_to_db.py` cho Anne (ghi vào Neon, cần Tri
cho phép). Chưa sync thì prod vẫn hiện "Đang soạn cho bạn…".

## Báo cáo khi xong

Một mục trong worklog của ngày: commit theo từng task (hash + tiêu đề); số pass/fail
backend và vitest ở Task 0 và sau Task 4; tên từng test đã sửa kèm lý do một dòng;
mọi lần dừng và lý do; mọi chỗ làm khác kế hoạch và lý do.

Không merge, không sync persona, không đẩy `release`. Các việc đó chờ Tri yêu cầu.
