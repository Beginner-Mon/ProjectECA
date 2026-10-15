# Memory v2 — STM một bản tóm tắt cuộn, LTM hai tầng

Plan để Tri giao cho một coding agent (opencode). Agent làm lần lượt từng bước, commit sau mỗi
bước và không push. Plan dừng ở chỗ tính năng chạy được trên máy này với Neon branch `memory-dev`
và có số benchmark để Tri duyệt. Staging trên AWS và deploy là plan riêng, chỉ làm sau khi Tri
duyệt tính năng.

## Context

Nhánh `feature/memory-v2` (tip `f8662e5b`, chưa push) hiện có:
- STM: các mẩu `summaries` nối đuôi nhau, cộng tin nhắn gốc.
- Tra hội thoại cũ: tool `memory_search` trên bảng `turn_index`.
- LTM: một danh sách fact phẳng trong `user_memory`, rút sau mỗi lượt.

Tri chỉ ra ba vấn đề:
1. Một hội thoại có nhiều mẩu tóm tắt. Mỗi hội thoại chỉ nên có một bản, lần nén sau gộp luôn bản
   trước.
2. `memory_search` chỉ được gọi khi user nhắc lại chuyện cũ. User nói tiếp một việc cũ trong hội
   thoại mới mà không nhắc thì agent không biết.
3. LTM không tách lời dặn áp dụng ở mọi nơi khỏi những điều chỉ có ích trong một project.

Thiết kế Tri chốt ngày 14/10/2026:
- **STM**: một bản tóm tắt cuộn lưu trong `conversations`, cộng các tin sau nó.
- **Tầng 1**: lời dặn cho agent, áp dụng ở mọi hội thoại. Luôn có trong prompt, nguyên văn.
- **Tầng 2**: fact, và lời dặn chỉ áp dụng trong một project. Mỗi mục có ngày và nhãn project, được
  tìm tự động mỗi lượt theo độ giống với câu của user.
- **Bỏ**: `memory_search`, `turn_index`, mọi việc đọc và ghi `summaries`. **Giữ**:
  `resume_last_session`.

Kết quả mong muốn:
- Lời dặn chung có mặt ở mọi hội thoại.
- Nhắc tới một việc cũ thì fact của việc đó tự vào prompt.
- STM và LTM không đưa trùng một điều vào cùng một prompt.
- Mọi thay đổi có số đo so với hệ hiện tại.

## Đặc tả

### Ba nơi lưu

| | Ở đâu | Nội dung |
| --- | --- | --- |
| Lịch sử gốc | `messages` | Không đổi |
| STM | `conversations.stm_summary`, `conversations.stm_up_to_seq` | Một bản tóm tắt cho mọi tin có `seq_id ≤ stm_up_to_seq` |
| LTM | `user_memory` | Mỗi dòng là một lời dặn hoặc một fact |

### Migration `016_memory_tiers`

**Cột mới của `user_memory`:**

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| `kind` | `TEXT NOT NULL DEFAULT 'fact'`, CHECK `instruction`/`fact` | Lời dặn hay fact |
| `tier` | `SMALLINT NOT NULL DEFAULT 2`, CHECK 1/2 | Tầng |
| `status` | `TEXT NOT NULL DEFAULT 'active'`, CHECK `candidate`/`active` | Ứng viên hay chính thức |
| `project` | `TEXT NULL`, CHECK `project IS NULL OR char_length(project) <= 60` | Nhãn project hoặc chủ đề. Kiểm ở ba chỗ: CHECK trong DB, `enforce` của extractor, schema của CRUD |
| `seen_count` | `INT NOT NULL DEFAULT 1` | Số hội thoại khác nhau đã gặp mục này |
| `seen_projects` | `TEXT[] NOT NULL DEFAULT '{}'` | Mỗi hội thoại được đếm thêm một phần tử: nhãn project lúc đó, `''` nếu không có |
| `last_seen_session_id` | `UUID NULL`, FK `conversations` ON DELETE SET NULL | Hội thoại gần nhất gặp mục này. Xoá hội thoại đó thì cột về NULL, dòng vẫn còn. Chỉ xoá hội thoại ở `source_session_id` (015, CASCADE) mới làm mất dòng |
| `said_at` | `TIMESTAMPTZ NOT NULL DEFAULT now()` | Lúc user nói nội dung hiện tại. Đây là ngày hiện trong prompt |
| `embedding` | `vector(384) NULL` | Chỉ dùng cho tầng 2. NULL nghĩa là cần embed |

**Phần còn lại của migration:**
- **Index**: `idx_user_memory_tier (user_id, tier, status) WHERE valid = true`. Không tạo index
  vector: mỗi user có ít dòng, quét chính xác qua index `user_id` là đủ.
- **Trigger** `BEFORE UPDATE`: khi `fact_text` hoặc `project` đổi thì đặt `embedding := NULL`.
  Cần trigger vì `routes_crud.py` bị test cấm viết chữ `embedding` trong SQL
  (`test_crud_app.py:137-189`).
- **`conversations`**: thêm `stm_summary TEXT NULL` và `stm_up_to_seq BIGINT NOT NULL DEFAULT 0`.
- **Backfill** (chỉ ghi vào cột mới, không xoá dòng nào của user):
  - `user_memory`: `restriction` → `kind='instruction', tier=1`; `condition` → `tier=1`; còn lại
    → `tier=2`.
  - `said_at = COALESCE(created_at, now())`, `last_seen_session_id = source_session_id`.
  - `conversations`: `stm_summary` = các mẩu `summaries` đang `active` nối theo `covers_from_seq`
    (cách nhau một dòng trống). `stm_up_to_seq` = `MAX(covers_up_to_seq)`.
- **Xoá bảng `turn_index`**: `DROP TABLE IF EXISTS turn_index CASCADE`. Đây là dữ liệu dẫn xuất,
  dựng lại được từ `messages`.
  - Giữ nguyên file `014_turn_index.py` và chuỗi 013 → 014 → 015 → 016. Không sửa
    `down_revision` của migration nào.
  - Trên prod, 014 sẽ tạo một bảng rỗng rồi 016 xoá nó ngay. Như vậy an toàn hơn việc viết lại
    lịch sử migration.
  - Downgrade của 016 tạo lại bảng bằng đúng các lệnh của 014.
- **Không xoá** bảng `summaries`. Code không còn đọc hay ghi nó, nhưng giữ lại để quay lui được.
  Một migration sau sẽ xoá nó khi prod đã ổn.
- **Trạng thái đã kiểm, chỉ đọc, ngày 14/10/2026:**
  - Prod: `alembic_version = 013_character_knowledge`, chưa có bảng `turn_index`.
  - Branch dev `memory-dev`: ở `015_user_memory_ltm`, có `turn_index`.
  - Cả hai nơi đều có 0 dòng `user_memory.valid = false`.
- Mỗi lệnh SQL nằm trong một `op.execute` riêng. Downgrade làm ngược lại theo thứ tự ngược.

**`said_at` khác `updated_at`:**
- `said_at` là lúc user nói nội dung hiện tại. Nó dùng cho ngày hiện trong prompt, hạn 60 ngày của
  ứng viên, và để chọn 60 dòng mới nhất đưa cho extractor.
- `updated_at` (có từ 015) là lúc dòng bị đổi gần nhất, bởi bất kỳ ai: extractor, `REPEAT`, nâng
  hoặc hạ tầng, user sửa, ẩn.
- Mọi lệnh ghi phải đặt `updated_at = now()`. Route sửa fact dựa vào cột này để báo 409 khi có
  người khác vừa đổi dòng đó.

### Luật vào tầng

Mọi luật nằm trong một hàm thuần `apply_policy(row_or_none, op, ctx) -> write`, test theo bảng.
"An toàn" nghĩa là category `restriction` hoặc `condition`. Ngưỡng nằm trong config:
`promote_fact_at: 2`, `promote_instruction_at: 3`, `candidate_ttl_days: 60`.

| Thao tác từ LLM | Code làm |
| --- | --- |
| `ADD`, mục an toàn | `tier=1`, `active`, ngay từ lần đầu |
| `ADD` fact, `explicit=true` | `tier=2`, `active` |
| `ADD` fact, `explicit=false` | `tier=2`, `candidate` |
| `ADD` lời dặn, không an toàn | `tier=2`. `explicit` thì `active`, không thì `candidate`. Không bao giờ vào thẳng tầng 1 |
| `REPEAT id`, cùng hội thoại với `last_seen_session_id` | Không làm gì. Mỗi hội thoại chỉ đếm một lần |
| `REPEAT id`, hội thoại khác | `seen_count += 1`, nối nhãn project vào `seen_projects`, cập nhật `last_seen_session_id` và `said_at`. Ứng viên đạt `promote_fact_at` thì thành `active` |
| Lời dặn sau `REPEAT` có `seen_count ≥ 3` | Lên `tier=1` (đặt `project=NULL`), **trừ khi** mọi phần tử của `seen_projects` là cùng một nhãn khác rỗng. Khi đó nó ở lại `tier=2`, `active`, với nhãn đó |
| `UPDATE id` (user đổi nội dung) | Ghi `fact_text` mới, `said_at`, `last_seen_session_id`. Nếu dòng đang `tier=1` mà không phải mục an toàn, đây là lời dặn bị đổi: hạ về `tier=2`, `candidate`, `seen_count=1`, `seen_projects=[nhãn]` |
| `DELETE id` | Ẩn: `valid = false`, `updated_at = now()`, như `_RETIRE_SQL` đang làm |
| `FORGET_ALL` | Ẩn mọi dòng `source='extracted'` đang `valid` của user. Các thao tác khác trong cùng lần trích bị bỏ |
| Ứng viên có `said_at` cũ hơn 60 ngày | Xoá thật |
| Dòng đã ẩn có `updated_at` cũ hơn 30 ngày | Xoá thật (`forget_purge_days: 30`) |

Hai dòng cuối là **một câu `DELETE` riêng, quét mọi dòng của user**, không giới hạn ở 60 dòng đưa
cho LLM:
```sql
DELETE FROM user_memory WHERE user_id = $1 AND (
  (status = 'candidate' AND said_at < now() - make_interval(days => $2))
  OR (valid = false AND updated_at < now() - make_interval(days => $3)))
```
Câu này chạy mỗi lần bước trích chạy cho user đó, kể cả khi lát tin không có tin nào của user.

Xoá do LLM quyết định thì ẩn trước, vì LLM có thể hiểu nhầm câu của user. Tri chốt ngày
14/10/2026. Nút xoá trên màn hình Ghi nhớ và route `DELETE /me/memory` vẫn xoá thật ngay.

Ví dụ phải đúng:
- "Gọi tôi là Tri", rồi "Du", rồi "Long", ở ba hội thoại khác nhau: mỗi lần là `UPDATE` một ứng
  viên, không lần nào lên tầng 1.
- "Ưu tiên serverless cho project A", nói ở 3 hội thoại đều về A: ở `tier=2`, nhãn A.
- "Trả lời ngắn thôi", nói ở các hội thoại về A, về gối và chuyện chung: lên `tier=1`.
- "Đừng gợi ý bài cho lưng", nói một lần: lên `tier=1` ngay.

Giữ nguyên các luật đang có trong `enforce`:
- Không `UPDATE` hay `DELETE` dòng `source='user'`.
- `fact_text` tối đa 300 ký tự.
- Bỏ số giấy tờ, điện thoại, email.
- Id không có thật thì bỏ.

Nâng `MAX_OPS` lên 8, vì đã có thêm `REPEAT`.

### Bước trích (`nodes/fact_extractor.py`)

Vẫn một lời gọi role `memory` mỗi lượt, vẫn dùng marker `ltm_up_to_seq`. Luôn đọc cả hội thoại
hiện tại: bỏ config `ltm_include_current_session`.

**Đầu vào gửi cho LLM:**
- Mọi dòng tầng 1.
- 60 dòng tầng 2 có `said_at` mới nhất, cả ứng viên lẫn chính thức.
- Danh sách nhãn project user đang có.
- Ngày của lát tin, lấy theo tin user mới nhất trong lát.

**Đầu ra JSON:**
```
{"operations": [
  {"op": "ADD", "kind": "instruction|fact", "fact": "...", "category": "...",
   "project": "<nhãn đã có hoặc nhãn mới ngắn>|null", "explicit": true|false},
  {"op": "UPDATE", "id": "f3", "fact": "...", "category": "...", "project": ...},
  {"op": "REPEAT", "id": "f2", "project": ...},
  {"op": "DELETE", "id": "f5"},
  {"op": "FORGET_ALL"}
]}
```

**Categories:** `condition, restriction, goal, preference, profile, style, project`. Hai category
mới:
- `style`: cách ECA nên nói chuyện, ví dụ cách xưng hô, độ dài, ngôn ngữ.
- `project`: một việc user đang theo đuổi và các lựa chọn user đã chốt cho việc đó.

**Prompt phải nói rõ:**
- Chỉ ghi điều user nói hoặc xác nhận về mình, hoặc điều user muốn ECA làm.
- Gợi ý của agent mà user chưa nhận thì không ghi.
- Quyết định chỉ đúng trong hội thoại này thì không ghi ("hôm nay làm bài 1 trước").
- Quyết định hay kế hoạch user đã nhận và còn hiệu lực về sau thì ghi thành fact có nhãn project.
- Nói lại điều đã có thì `REPEAT`, không `ADD`. Nói khác đi thì `UPDATE` với nội dung đầy đủ.
- Đổi mốc thời gian tương đối ra ngày cụ thể.
- Không chép văn bản user dán vào. Nhiều nhất một fact ngắn nói user đang làm gì với văn bản đó.
- Viết bằng ngôn ngữ của user.

**Sau khi ghi:** embed các dòng `tier=2` có `embedding IS NULL` của user, tối đa 20 dòng mỗi lần,
bằng `aembed_passages`. Văn bản để embed là `"<project>: <fact>"`, hoặc chỉ `<fact>` nếu không có
nhãn. Bước embed này chạy cả khi `VVA_LTM` tắt, để fact user tự gõ cũng được embed.

**Log** `fact_extraction_done` kèm `input_tokens`, `output_tokens` (đọc từ `usage_metadata` của
`raw`) và `elapsed_ms`.

### STM (`nodes/summarizer.py`)

- `compact_stm(session_id, history_budget, compact_at)` thay cho `maybe_summarize`:
  1. Đọc `stm_summary` và `stm_up_to_seq`, rồi các tin sau `stm_up_to_seq`.
  2. Dùng lại `_compaction_cut`.
  3. Có điểm cắt thì gọi LLM role `memory` một lần. Đầu vào: bản cũ + các tin tới điểm cắt. Đầu ra:
     **một** bản tóm tắt mới gộp cả bản cũ.
  4. Ghi bằng compare-and-set:
     `UPDATE conversations SET stm_summary=$2, stm_up_to_seq=$3 WHERE session_id=$1 AND stm_up_to_seq=$4`.
     Không ghi được thì log `stm_compaction_superseded`.
  5. Không embed.
- Prompt dựa trên prompt ở `_summarize_messages` (summarizer.py:213-273). Phải giữ lời dặn và
  fact user kể về mình, ngoài những thứ prompt đó đang giữ. Tối đa khoảng 250 từ, viết bằng ngôn
  ngữ của hội thoại.
- Log `stm_compacted` kèm `input_tokens`, `output_tokens`, `elapsed_ms`.
- `housekeep`:
  - Bỏ nhánh `turn_index` và `summarize_finished_sessions`.
  - Chạy `compact_stm` và `extract_user_facts` (khi `VVA_LTM` bật) song song bằng
    `asyncio.gather`, rồi chạy bước embed.
  - Giữ `bind_request_llm(None)` và cách mỗi bước tự bắt lỗi của mình.
- `resume_last_session` đọc `stm_summary` và 10 tin **cuối** sau `stm_up_to_seq`
  (`ORDER BY seq_id DESC LIMIT 10`, rồi đảo lại). Giữ nguyên dạng kết quả: `summary_chunks` là
  `[stm_summary]` hoặc `[]`.
- `db/gdpr.py::delete_message`: nếu tin bị xoá có `seq_id ≤ stm_up_to_seq` thì đặt
  `stm_summary=NULL, stm_up_to_seq=0`. Bỏ `get_dirty_chunks`, `re_summarize_chunk`,
  `summarizer.rebuild_dirty_chunk`, và các task dựng lại mẩu tóm tắt trong route xoá tin ở
  `api/main.py` (khoảng dòng 505-535).

### Đọc: node memory (`nodes/memory.py`)

Ba nhánh chạy trong một `asyncio.gather`.

**1. Tầng 1**
- Đọc các dòng `tier=1`, `status='active'`, `valid`.
- Mục an toàn đứng trước, sau đó xếp theo `said_at` giảm dần.
- Ghi vào state `user_instructions: list[str]`.

**2. STM**
- Một câu SQL lấy `stm_summary` cùng các tin sau `stm_up_to_seq`, bỏ câu hỏi đang chạy (giữ cách
  loại `user_message_id` như hiện nay).
- `session_summary` là `[stm_summary]` hoặc `[]`.
- Giữ `_drop_history_overflow` và nhánh dự phòng `_load_latest_turns`.

**3. Tầng 2**
- Embed câu gốc của user (`config["configurable"]["query"]`) bằng `aembed_query`, bọc trong
  `asyncio.wait_for` với trần `memory.tier2_embed_timeout_ms` (300).
- Quá trần hoặc lỗi thì trả `[]` và log lý do.
- Một câu SQL:
  - Lấy `tier2_top_k` (5) dòng `tier=2`, `active`, có embedding, và
    `last_seen_session_id IS DISTINCT FROM <hội thoại hiện tại>`, xếp theo `embedding <=> q`.
  - Giữ các dòng có độ giống ≥ `tier2_min_similarity` (0.86, sẽ chỉnh theo benchmark).
  - Kéo thêm mọi dòng `active` cùng nhãn project với các dòng trúng, với cùng điều kiện
    `last_seen_session_id`. **Chỉ kéo theo nhãn khác NULL**
    (`project IS NOT NULL AND project IN (nhãn của các dòng trúng)`). Dòng trúng không có nhãn
    thì chỉ đưa chính nó vào.
- Thứ tự: dòng trúng trực tiếp xếp theo độ giống, rồi dòng cùng project xếp theo `said_at`.
- Mỗi dòng có dạng `- [2026-10-14] (nhãn) nội dung`.
- Ghi vào state `user_facts: list[str]`.

Log `node_complete` thêm các trường:
- `user_instructions_count`, `user_facts_count`, `tier2_top_similarity`, `tier2_embed_ms`
- `tier2_skipped` kèm lý do
- `summary_present`

**Không trùng STM và LTM.** Một fact được thấy lần cuối ở hội thoại hiện tại thì STM đã có nó, nên
tầng 2 bỏ qua nó (điều kiện `last_seen_session_id` ở trên). Tầng 1 thì luôn hiện, vì mỗi lời dặn
chỉ một dòng và lời dặn an toàn không được phụ thuộc vào bản tóm tắt.

### Prompt và state

- **`state.py`**: thêm `user_instructions: list[str]`. `user_facts` giờ là các dòng tầng 2.
  `session_summary` giữ kiểu `list[str]`, có 0 hoặc 1 phần tử.
- **Synthesizer**: thêm `_build_user_instructions` cạnh `_build_user_facts` (synthesizer.py:437).
  Thứ tự `middle_blocks`: `body_note, app_note, about_you, user_instructions, user_facts,
  session_summary`. Thêm `user_instructions` vào hai dict `prompt_blocks`.
  - Tiêu đề tầng 1: `## Standing instructions from this user`, kèm câu "Follow these in every
    answer unless today's message says otherwise. Health notes are the user's own account, not a
    diagnosis."
  - Tiêu đề tầng 2: `## Things this user mentioned in earlier conversations`, kèm câu "Each line
    has the date it was said. Newer beats older, and today's message beats both. Use one only when
    it changes what you say."
  - Không khối nào được chứa chữ "session".
- **Planner** (planner.py:279-282): thêm dòng "Standing instructions from the user:" cạnh "Known
  about the user". Dữ liệu tầng 2 đi vào phần "Known about the user".
- **Ngân sách** (`shared/context.py` và `config/langgraph.yaml`): thêm `user_instructions: 300`,
  đổi `user_facts` lên 400. Viết hàm render tầng 1 theo mẫu `render_user_facts`: giữ nguyên từng
  dòng, cắt từ cuối.

### Gỡ bỏ

**Code:**
- `memory_search` cùng mọi phần chỉ phục vụ nó trong `tools/pgvector_tool.py`:
  `_search_summaries`, `_search_turn_index`, `_embed_query_and_words`, `_ambiguous`,
  `_history_min_similarity`.
- Export trong `tools/__init__.py`, và mục trong `RETRIEVER_BASE_TOOLS` (retriever_agent.py:48-49)
  cùng docstring nhắc tới nó.
- Ánh xạ của `memory_search` trong `sources.py`. Giữ ánh xạ của `resume_last_session`.
- Các hàm trong `evidence.py` chỉ dùng cho kết quả của `memory_search` (`_turn_items`,
  `_summary_items`). Giữ phần `resume_last_session` cần.
- File `db/turn_index.py`.
- Trong summarizer: `summarize_finished_sessions`, việc ghi `summaries` trong `_run_summarize`,
  `maybe_summarize`.
- Trong memory.py: `_load_summary_chunks`.

**Config:**
- `memory.history_index_enabled` (env `VVA_HISTORY_INDEX`)
- `memory.ltm_include_current_session`
- `retrieval.memory_min_similarity`
- `retrieval.history_min_similarity`

**Test:** xoá test chỉ ghim hành vi đã gỡ: `test_turn_index.py`, `test_turn_index_integration.py`,
`test_memory_search_recall.py`. Giữ `test_turn_index_migration.py`, vì file 014 vẫn còn. Xoá thêm
các phần về `memory_search` hay mẩu tóm tắt trong `test_pr1_memory_fix.py`,
`test_user_words_reach_models.py` và `test_a1_a2_a3.py`. Test chỉ dùng chữ `"memory_search"` làm
tên tool giả trong fixture thì để nguyên nếu vẫn pass. Không nới lỏng test của hành vi được giữ.

**Không đụng** code chết có từ trước và không liên quan: `_MEM_CFG`, `assemble_context_messages`,
v.v.

### CRUD, màn hình, chính sách

**`api/routes_crud.py` và `api/schemas.py`:**
- List: trả thêm `kind`, `tier`, `status`, `project`, `said_at`. Trả cả ứng viên.
- Create: nhận `kind` (mặc định `fact`) và `project` (tuỳ chọn, tối đa 60 ký tự). User tự gõ thì
  `source='user'`, `active`. Lời dặn vào `tier=1`, fact vào `tier=2` (embedding được bổ sung ở
  lượt chat sau).
- Edit (`POST /me/memory/{id}`): giữ nguyên cơ chế chống ghi đè mà phần sửa fact ở bước 0 đã
  làm:
  - Body gửi `expected_updated_at`, là `updated_at` frontend nhận được lúc list.
  - Route `SELECT … FOR UPDATE` dòng đó rồi so bằng `=` với `updated_at` trong DB. Khác thì trả
    409, không có dòng thì trả 404.
  - Không dùng header `If-Match`.
  - Chỉ thêm `said_at = now()` và `updated_at = now()` vào lệnh UPDATE. Trigger tự xoá embedding.
  - Vì mọi lệnh ghi của extractor cũng đặt `updated_at`, user sửa đúng lúc bước trích vừa đổi
    dòng đó thì nhận 409. Đây là hành vi đúng; phải có test.
  - Nếu lúc giao plan route này khác với mô tả trên thì dừng và hỏi Tri.
- Route mới `DELETE /me/memory`: xoá mọi dòng của user, trả số dòng đã xoá. Thêm
  `memory.add_method("DELETE", crud, **authed)` vào `infra/infra/rest_api_stack.py` và cập nhật
  danh sách route trong `test_crud_app.py`.

**Frontend:**
- `lib/userMemory.ts`: thêm `kind`, `tier`, `status`, `project`, `said_at` vào kiểu dữ liệu.
  Backend cũ không trả các trường này thì dùng giá trị mặc định: `fact`, 2, `active`, `null`,
  `created_at`. Thêm hàm `groupFacts` trả về `{instructions, byProject: [{project, items}],
  candidates}`, kèm test vitest.
- `MemoryContent.tsx`: giữ kiểu trang tài liệu hiện có, chia ba phần:
  - "Lời dặn ECA luôn theo"
  - "Điều ECA biết về bạn", nhóm theo project
  - "ECA để ý thấy", kèm ghi chú: tự xoá sau 60 ngày nếu không gặp lại
- Mỗi dòng hiện ngày. Form thêm mới có lựa chọn lời dặn hoặc fact. Thêm nút "Xoá tất cả" có hỏi
  xác nhận.
- Thêm khoá i18n `memory.*` vào cả `en.json` và `vi.json`.

**Chính sách:** sửa `ECA_UI/frontend/src/legal/privacy.en.md` và `privacy.vi.md`, dòng 10, 11, 55
và 56:
- Thay câu "tóm tắt mỗi hội thoại đã xong để nhớ về sau" bằng một bản tóm tắt cuộn trong mỗi hội
  thoại. ECA dùng nó để theo dõi hội thoại đó và để tiếp tục hội thoại gần nhất.
- Nói về hai loại ghi chú, ngày đi kèm, và việc ứng viên tự xoá sau 60 ngày.
- Nói rõ hai cách xoá khác nhau:
  - Nút xoá và "Xoá tất cả" trong Cài đặt xoá ngay mọi ghi chú, kể cả ghi chú user tự gõ.
  - Bảo ECA "quên đi" trong lúc chat thì ghi chú ECA tự ghi bị ẩn ngay và bị xoá hẳn sau 30 ngày.
    Ghi chú user tự gõ không bị đụng.
- Ghi rõ trong worklog rằng đây là bản nháp, chờ Tri và Nguyen duyệt câu chữ.

## LLM cho plan này: quay lại DeepSeek (cập nhật 15/10/2026)

- Tri đã nạp 2 USD cho DeepSeek. Số dư lúc bắt đầu chạy lại A là 1,99 USD.
- Mọi lần chạy có LLM dùng **DeepSeek, như prod**. Đó là mặc định của helper, không cần cờ
  `--llm`.
- **Chỉ dùng `deepseek-v4-flash`, cho mọi role** (Tri chốt 15/10). Mỗi lần chạy phải đặt
  `DEEPSEEK_MODEL=deepseek-v4-flash` và `DEEPSEEK_MODEL_FAST=deepseek-v4-flash`. Mặc định,
  synthesizer dùng `deepseek-v4-pro`. A và B phải cùng cài đặt này. Câu trả lời trong benchmark vì
  vậy khác prod, nơi synthesizer chạy pro.
- A được chạy lại toàn bộ trên DeepSeek. Dữ liệu dựng bằng Gemini trước đó bị bỏ, vì A và B phải
  chạy cùng model.
- **Ngân sách**: trước và sau mỗi lần chạy benchmark hoặc probe, xem số dư bằng
  `GET https://api.deepseek.com/user/balance`, rồi ghi mức chênh vào worklog. Số dư dưới
  0,5 USD thì dừng và báo Tri.
- Benchmark giữ bản thu nhỏ (khoảng 42 lời gọi mỗi lần chạy). Bỏ quy tắc giãn 20 giây giữa các
  lượt, vì quy tắc đó chỉ dành cho Gemini miễn phí.
- Chế độ `--llm gemini --gemini-key N` vẫn giữ trong helper, chỉ dùng khi DeepSeek hết tiền lần
  nữa. Mục dưới đây là ghi chép của lần dùng Gemini.

## (Ghi chép) LLM cho plan này: Gemini 2.5 Flash (Tri chốt 14/10/2026)

Tài khoản DeepSeek hết tiền (402), và Tri quyết định không nạp. Mọi lần chạy có LLM trong plan
này dùng `gemini-2.5-flash`.

**Đã đo ngày 14/10:**
- 4 key trong `GEMINI_API_KEYS` đều là gói miễn phí: **20 lời gọi mỗi ngày cho mỗi key**, với
  `gemini-2.5-flash`. Key 1 đã hết hạn mức hôm nay.
- `gemini-2.5-pro` trả 404 trên cả 4 key.
- Gọi tool và stream chạy được. Có lúc gặp 503 (model quá tải) và 504 (quá hạn).

**Sửa code (làm đầu bước 1, commit riêng):**
- `llm.py`: đổi `_FALLBACK_MODEL_HEAVY` từ `gemini-2.5-pro` sang `gemini-2.5-flash`. Việc này cũng
  sửa đường dự phòng đang hỏng trên prod.
- `llm.py`: khi Gemini là model chính (không có key DeepSeek), dùng `max_retries=2` thay cho 0, để
  qua được 503/504. Khi Gemini chỉ là dự phòng thì giữ 0 như cũ.
- Helper `on_dev_branch.py` thêm cờ `--llm gemini --gemini-key N`. Sau `load_env()`, helper xoá
  `DEEPSEEK_API_KEY` và `DEEPSEEK_API_KEY_PARAM`, rồi gán `GEMINI_API_KEYS` bằng đúng key thứ N.
  Helper in ra số thứ tự key, không in key.

**Ngân sách:**
- Mỗi lần gọi helper dùng một key. Ghi số lời gọi của từng lần chạy vào worklog.
- Hết hạn mức (429) thì dừng, chờ sang ngày (hạn mức reset lúc nửa đêm giờ Pacific). Không
  xoay vòng key trong cùng một tiến trình.
- Lượt nào gặp 429, 503 hoặc 504 sau khi đã thử lại thì đánh dấu **invalid**: không chấm điểm, và
  chạy lại lượt đó đúng một lần ở lần chạy sau.

**Không chạy lại** các báo cáo đã đo trên DeepSeek: `memory-probe-V0…V2`,
`context-probe-V0…V8`, `history-search-probe-V0`. Chúng giữ nguyên làm tài liệu. Chỉ benchmark A
chạy lại trên Gemini, vì A và B phải chạy cùng một model mới so được. Cụm A chạy trên DeepSeek
ngày 14/10 không dùng được: breaker đang mở, log trống, và gặp 402 giữa chừng.

**Hệ quả:** A và B cùng chạy trên Gemini Flash, nên so sánh hai thiết kế memory vẫn công bằng.
Nhưng chất lượng, độ trễ và token không đại diện cho prod (DeepSeek). Báo cáo phải ghi rõ điều
này.

## Quy tắc cho agent

- **Trước khi bắt đầu**:
  - Chạy `git status`. Ngoài `.claude/CLAUDE.md` và `docs/architecture/aws-layered.drawio`, nếu
    còn file nào chưa commit thì dừng và báo Tri.
  - Đọc `.claude/CLAUDE.md`.
- **Nhánh**: chỉ làm trên `feature/memory-v2`.
  - Không checkout, merge hay push `release`.
  - **Không push nhánh nào, kể cả `feature/memory-v2`.** Hoãn bước 3–5 của ship flow là có chủ
    ý: chúng nằm trong plan deploy, sau khi Tri duyệt benchmark B.
  - Không bao giờ chạy `git push origin HEAD:release`.
- **Commit**:
  - Mỗi bước một commit trở lên, chỉ `git add` đúng path của bước đó. Không `git add -A`.
  - Không đụng `.claude/CLAUDE.md`, `docs/architecture/aws-layered.drawio` và mọi thứ dưới
    `SpeechLLm/`.
  - Message theo kiểu repo: `feat(memory): …`, `fix(…)`, `docs(memory-v2): …`.
- **Database. `agenticRAG/.env` trỏ vào Neon prod.**
  - Không chạy backend, script hay alembic bằng `.env` trần.
  - Mọi việc cần DB đi qua helper `local_tests/on_dev_branch.py` (tạo ở bước 1). Helper tự chuyển
    sang `ECA_TEST_PG_DSN` / `ECA_TEST_PG_DSN_OWNER` và từ chối khi host là prod hoặc `-pooler`.
  - Helper từ chối thì dừng và báo, không tìm đường vòng.
- **Python**: dùng `C:\Miniconda\envs\firstconda\python.exe`. Shell là PowerShell 5.1, không có
  `&&`.
  - Unit test:
    `$env:VVA_PG_DSN="postgresql://eca:eca_dev@127.0.0.1:9/eca"; & C:\Miniconda\envs\firstconda\python.exe -m pytest tests/langgraph_agents -m unit -q`
    Port 9 đóng: bộ unit không hoàn toàn tách khỏi DB, nên phải chặn nó với tới Neon.
  - Chạy lẻ vài file thì thêm `-W "ignore::PendingDeprecationWarning"`.
  - Lỗi có sẵn, bỏ qua:
    `test_phase6_circuit_breaker::test_planner_degrades_when_no_provider_can_be_built`.
  - File test không được import lẫn nhau, vì CI gọi `pytest` trực tiếp.
- **Frontend**: trong `ECA_UI/frontend` chạy `npm run build`, `npm test`, `npm run lint`. Không
  dùng `tsc --noEmit`.
- **Viết test trước** cho mọi hành vi mới.
  - Thấy lỗi ngoài phạm vi thì ghi vào báo cáo, không sửa.
  - Probe hay benchmark sai ở một ví dụ thì báo con số. **Không sửa prompt riêng cho ví dụ đó.**
- **Worklog**: ghi vào `docs/worklogs/<DD-MM-YYYY>-memory-v2.md` của ngày làm việc. Gồm: đã làm
  gì, lệnh test kèm kết quả thật, chỗ làm khác plan.
- **Dừng và báo Tri khi**:
  - một lệnh có thể chạm Neon prod;
  - helper từ chối;
  - đặc tả ở đây mâu thuẫn với code theo cách plan chưa nói tới;
  - benchmark A không chạy được.

## Các bước

### Bước 0 — Tri làm trước khi giao plan

- Working tree đang có thay đổi chưa commit của tính năng sửa fact: route
  `POST /me/memory/{fact_id}`, `MemoryContent.tsx`, `api.ts`, `userMemory.ts`, i18n,
  `rest_api_stack.py`, `test_crud_app.py`, `test_memory_edit.py`. Tri commit phần này hoặc bỏ nó.
- Báo Nguyen rằng `memory_search`, `summaries` và `housekeep` (code của Nguyen, `db1fce2` và
  `0a15a2e`) sẽ được thay.

### Bước 1 — Helper, benchmark, số đo của hệ hiện tại (A)

**Helper `agenticRAG/langgraph_agents/local_tests/on_dev_branch.py`:**
- Gọi `load_env()`.
- Lấy host prod từ `VVA_PG_DSN` / `VVA_PG_DSN_OWNER`. Từ chối khi host của
  `ECA_TEST_PG_DSN` / `ECA_TEST_PG_DSN_OWNER` bị thiếu, trùng host prod, hoặc chứa `-pooler`.
- Gán hai giá trị dev vào `os.environ["VVA_PG_DSN"]` và `os.environ["VVA_PG_DSN_OWNER"]`. Biến
  môi trường của process thắng `.env`.
- In ra host, không bao giờ in DSN.
- Các chế độ:
  - `pytest <args>`: `pytest.main`
  - `alembic <args>`: chuyển vào `agenticRAG/langgraph_agents` rồi gọi `alembic.config.main`
  - `script <path> <args>`: `runpy`
- Không có chế độ chạy SQL tuỳ ý.

**Log token:** thêm `input_tokens` và `output_tokens` vào log `fact_extraction_done` của
extractor và `summarizer_done` của summarizer, để A cũng có số chi phí.

**Benchmark** `local_tests/run_memory_benchmark.py` và `local_tests/memory_benchmark.yaml`:
- **Cách chạy**: chạy app FastAPI thật trong process bằng `fastapi.testclient.TestClient(app)`
  trong khối `with`, để lifespan chạy. Gọi `POST /chat`.
  - User id được override qua `app.dependency_overrides[current_user_id]`, giống
    `tests/langgraph_agents/test_phase5_sse.py:118-140`. Hàm override phải gọi
    `bind_request_user(uid)` (như `api/auth.py:239`) rồi mới trả `uid`, nếu không RLS chặn hết.
  - Mỗi kịch bản dùng một user mới: `uuid5(label + id kịch bản)`.
  - **Dọn dữ liệu cũ**: đầu mỗi kịch bản, xoá user đó bằng
    `DELETE FROM users WHERE id = $1` qua `VVA_PG_DSN_OWNER`. Cascade xoá luôn hội thoại, tin
    nhắn và ghi nhớ. Nhờ vậy chạy lại cùng label vẫn sạch.
  - **Chốt chặn thứ hai**: script tự thoát nếu host của `VVA_PG_DSN` khác host của
    `ECA_TEST_PG_DSN`, tức là không chạy qua helper.
  - Phản hồi SSE kết thúc sau khi `housekeep` xong, nên lượt sau thấy đủ dữ liệu của lượt trước.
- **Kịch bản: thu nhỏ để vừa hạn mức Gemini miễn phí.**
  - Hội thoại dựng sẵn **không chạy qua agent**.
    - Ghi thẳng vào `messages`: tin của user và một câu trả lời viết sẵn trong YAML. Dùng hàm ghi
      của `db/session_store.py`.
    - Sau đó gọi `housekeep(user_id, session_id)` **một lần cho mỗi hội thoại**. Với A thì đó là
      đánh chỉ mục và trích fact; với B là nén, trích và embed.
    - Mỗi hội thoại tốn khoảng 1 lời gọi LLM.
    - Câu trả lời viết sẵn còn giúp nhóm c chấm được chắc chắn.
  - `probes`: **mỗi nhóm a–f đúng 1 probe**, chạy `/chat` thật. Trước khi chạy probe, đặt
    `VVA_LTM=0` để lượt probe không gọi thêm bước trích. Mỗi probe gồm `group`, `ask`, `where`
    (`new` = hội thoại mới, `last` = hội thoại cuối), `expect_any`, `forbid`.
  - **Nhóm k:** dựng một hội thoại dài theo 3 đợt, gọi `housekeep` sau mỗi đợt (3 lần nén). Kiểm
    bằng từ khoá trong bản tóm tắt, không chạy lượt chat.
  - **Ước tính:** mỗi lần chạy A khoảng 6 lời gọi để dựng + 3 để nén + 6 probe × khoảng 3 lời gọi,
    tổng khoảng 27. B thêm khoảng 8 lời gọi cho nhóm g–j. Chia mỗi lần chạy ra 2 key bằng chế độ
    `--only/--append`: phần dựng một key, phần probe một key.
- **Chấm**: câu trả lời được chuẩn hoá (chữ thường, bỏ dấu tiếng Việt bằng NFD). Đạt khi có ít
  nhất một từ trong `expect_any` và không có từ nào trong `forbid`. Lưu nguyên văn câu trả lời
  vào báo cáo.
- **Nhóm, mỗi nhóm 1 probe:**
  - a. Nhắc lại rõ ràng chuyện cũ
  - b. Nói tiếp một việc cũ mà không nhắc lại
  - c. Hỏi điều agent đã nói: ở hội thoại gần nhất, và ở một hội thoại cũ hơn
  - d. Kể bằng tiếng Việt, hỏi bằng tiếng Anh, và ngược lại
  - e. Fact đã đổi: phải dùng giá trị mới
  - f. Câu không liên quan: không được kéo fact nào vào
  - k. Một hội thoại dài, đủ để nén ít nhất 3 lần: lời dặn và fact kể ở lượt 1 phải còn trong bản
    tóm tắt và trong câu trả lời ở cuối
  - Thêm ở bước 6, chỉ cho B: g (phạm vi của lời dặn), h (lời dặn an toàn nói một lần), i (tên
    đổi liên tục), j (quên một mục, quên tất cả). Các nhóm này kiểm bằng cách đọc thẳng
    `user_memory`.
- **Đo**: gắn một `logging.Handler` để thu các log sau:
  - `node_complete` của memory, planner và synthesizer
  - các trường tầng 2
  - token và thời gian của bước trích và bước nén
  - Thêm thời gian từ lúc gửi tới sự kiện `done`, và từ `done` tới lúc phản hồi kết thúc (thời
    gian `housekeep` giữ lại).
- **Báo cáo**: `docs/tracking/memory-benchmark-<label>.md` và `.json`. Gồm:
  - tỉ lệ đạt theo nhóm;
  - p50/p95 của node memory;
  - độ giống cao nhất của tầng 2 ở các probe nhóm b/e so với nhóm f;
  - số lời gọi LLM chạy nền và token mỗi lượt;
  - ước tính token cho mỗi 1.000 lượt.

**Chạy A:** ngay trên commit hiện tại, trước mọi thay đổi khác, với `VVA_HISTORY_INDEX=1` và
`VVA_LTM=1`:
`$env:VVA_HISTORY_INDEX="1"; $env:VVA_LTM="1"; & C:\Miniconda\envs\firstconda\python.exe agenticRAG/langgraph_agents/local_tests/on_dev_branch.py --llm gemini --gemini-key 2 script agenticRAG/langgraph_agents/local_tests/run_memory_benchmark.py --label A --only <phần dựng>`.
Sau đó chạy phần probe với `--gemini-key 3 … --only <phần probe> --append`. Hôm nay còn key
2, 3, 4; key 1 dùng lại được từ ngày mai.

Chạy ở chế độ nền, ghi log ra file. Một lượt chat trên máy này mất 10–20 s. Nếu agent không có
cách chạy nền riêng, dùng:
```
Start-Job -Name benchA -ScriptBlock {
  Set-Location 'D:\Swin documents\Virtual-Verbal-Assistant'
  $env:VVA_HISTORY_INDEX = '1'; $env:VVA_LTM = '1'
  & C:\Miniconda\envs\firstconda\python.exe agenticRAG/langgraph_agents/local_tests/on_dev_branch.py script agenticRAG/langgraph_agents/local_tests/run_memory_benchmark.py --label A *> "$env:TEMP\memory-benchmark-A.log"
}
```
Xem tiến độ bằng `Get-Content "$env:TEMP\memory-benchmark-A.log" -Tail 20`. Xong thì gọi
`Receive-Job benchA`. Benchmark B chạy theo cùng mẫu, đổi label và bỏ `VVA_HISTORY_INDEX`.

**Xong khi:** có `memory-benchmark-A.md` và `.json`, và đã commit cả helper lẫn benchmark.

### Cập nhật 15/10: A chạy riêng, plan không chờ A

- Sáng 15/10, API sinh câu trả lời của DeepSeek sập (mọi kiểu request đều treo; `/user/balance`
  vẫn trả 200). A chưa có số hợp lệ.
- **A do K chạy**, trong worktree `D:\Swin documents\VVA-benchA` cố định ở `15fcb77b`, khi có LLM
  dùng được. Agent không chạy A và không đụng worktree đó.
- **Agent làm tiếp bước 2–6 ngay.** Các bước này chỉ viết code và unit test, không cần LLM. Ngoại
  lệ: probe V3 ở bước 4 để sau.
- **Không chạy `alembic upgrade` trên `memory-dev` cho tới khi K báo A xong**, vì 016 xoá
  `turn_index` mà A cần. Bước 2 vẫn viết migration và test migration; phần "áp lên Neon branch
  dev" dời tới lúc K báo.

### Bước 2 — Migration

- Viết `016_memory_tiers.py` theo đặc tả, `down_revision = "015_user_memory_ltm"`. Không sửa và
  không xoá migration cũ nào.
- Test migration theo mẫu `tests/langgraph_agents/test_user_memory_ltm_migration.py`: cột, CHECK,
  FK, trigger, backfill, `DROP turn_index`, downgrade. Phải có một test khẳng định migration không
  chứa lệnh `DELETE` nào trên `user_memory`.
- Áp lên Neon branch dev:
  - `on_dev_branch.py alembic current`: phải là `015_user_memory_ltm`.
  - `on_dev_branch.py alembic upgrade head`.
  - Kiểm bằng truy vấn catalog chỉ đọc: có cột mới, có trigger, đã mất `turn_index`.
- **Xong khi:** `alembic current` báo `016_memory_tiers` trên branch dev và test migration pass.

### Bước 3 — STM cuộn

- Làm `compact_stm`, nhánh STM của node memory, `resume_last_session`, phần reset trong
  `gdpr.delete_message`. Gỡ phần ghi và đọc `summaries`.
- Sửa các test: `test_pr2_summarizer.py`, `test_memory_v2_stm.py`, `test_phase2_5_memory.py`,
  `test_session_end_summary.py`, `test_window_budgets.py`, `test_interrupted_turn.py`.
- Test mới:
  - Lần nén thứ hai gộp bản thứ nhất.
  - Compare-and-set: hai lần nén đồng thời thì chỉ một lần được ghi.
  - Xoá một tin đã nằm trong bản tóm tắt thì bản tóm tắt bị reset.
- **Xong khi:** bộ unit pass.

### Bước 4 — Ghi LTM

- Thao tác mới trong extractor, `apply_policy`, bước embed, xoá ứng viên quá hạn, `FORGET_ALL`.
  `housekeep` chạy nén và trích song song.
- Viết test theo bảng cho `apply_policy`, phủ mọi dòng của bảng luật vào tầng và bốn ví dụ phải
  đúng. Phải có test cho các trường hợp sau:
  - `DELETE` và `FORGET_ALL` chỉ ẩn, và `FORGET_ALL` không đụng dòng `source='user'`.
  - Dòng đã ẩn quá 30 ngày bị xoá thật.
  - Mọi lệnh ghi đều đặt `updated_at`.
- Sửa `test_fact_extractor.py`.
- Probe:
  - Cập nhật `run_memory_probe.py` và `memory_probes.yaml` theo dạng thao tác mới.
  - Thêm probe cho: lời dặn chung; lời dặn trong project; nói lại điều đã có (phải ra `REPEAT`);
    đổi tên; quên tất cả; quyết định user đã nhận; quyết định chỉ trong hội thoại.
  - Chạy `run_memory_probe.py --label V3 --mode turn --only <các probe mới>`, tối đa 10 probe,
    qua helper với `--llm gemini`. Probe này không cần DB. Không so V3 với V2 theo con số, vì V2
    chạy trên DeepSeek.
- **Xong khi:** bộ unit pass và đã commit `memory-probe-V3.md`.

### Bước 5 — Đọc LTM, gỡ `memory_search`

- Nhánh tầng 1 và tầng 2 trong node memory. Sửa `state.py`, synthesizer, planner, budgets và
  config. Gỡ những phần ở mục "Gỡ bỏ".
- Test mới:
  - Tầng 1 luôn có trong prompt của synthesizer và planner.
  - Tầng 2 bỏ qua dòng có `last_seen_session_id` là hội thoại hiện tại.
  - Một dòng trúng kéo theo cả project của nó.
  - Embed quá trần thì lượt vẫn chạy, không có tầng 2.
  - Không khối prompt nào chứa chữ "session".
- **Xong khi:** bộ unit pass.

### Bước 6 — CRUD, frontend, chính sách, nhóm g–j

- Làm như mục "CRUD, màn hình, chính sách". Thêm nhóm g–j vào benchmark.
- **Xong khi:** bộ unit pass, và `npm run build`, `npm test`, `npm run lint` đều pass.

### Bước 7 — Kiểm chứng toàn bộ và báo cáo

1. Bộ unit đầy đủ, theo lệnh ở phần quy tắc.
2. Test integration trên branch dev:
   `on_dev_branch.py pytest tests/langgraph_agents -m integration -q -W "ignore::PendingDeprecationWarning"`.
   - Thêm file mới `test_memory_tiers_integration.py`, dùng guard
     `ECA_TEST_PG_DSN == VVA_PG_DSN` như `test_fact_extractor_integration.py`.
   - File này kiểm:
     - RLS: user khác không đọc được dòng tầng 1 hay tầng 2.
     - Trigger xoá embedding.
     - Compare-and-set của STM.
     - Extractor ghi đúng tầng.
     - Tầng 2 bỏ qua hội thoại hiện tại.
     - Tầng 2 không kéo các dòng không nhãn theo nhau.
     - CHECK độ dài nhãn project.
     - Câu purge xoá được dòng cũ nằm ngoài 60 dòng mới nhất.
     - Xoá hội thoại là `source_session_id` của một fact thì fact mất. Xoá hội thoại chỉ là
       `last_seen_session_id` thì fact còn, và cột đó về NULL.
   - Test cần Postgres ở `:5433` sẽ tự skip, như trước giờ.
3. Frontend: `npm run build`, `npm test`, `npm run lint`.
4. Benchmark B:
   `$env:VVA_LTM="1"; & C:\Miniconda\envs\firstconda\python.exe agenticRAG/langgraph_agents/local_tests/on_dev_branch.py script agenticRAG/langgraph_agents/local_tests/run_memory_benchmark.py --label B`.
5. **Bỏ context probe ở plan này.** Nó cần hàng trăm lời gọi LLM, và các báo cáo cũ chạy trên
   DeepSeek nên không so được. Phần kiểm câu trả lời có tệ đi không được dời sang lúc chạy
   staging.
6. Nếu số ở nhóm b, e, f cho thấy ngưỡng 0.86 không tách được dòng đúng khỏi nhiễu: đổi
   `tier2_min_similarity` thành một con số duy nhất nằm giữa hai nhóm, chạy lại B và ghi lý do.
7. Chép plan này vào `docs/plans/memory-v2-tiers.md`. Viết worklog. Commit tài liệu.
8. Báo cáo cuối gồm:
   - danh sách commit;
   - bảng A so với B theo nhóm;
   - p50/p95 của node memory;
   - token mỗi lượt;
   - câu trả lời trượt, kèm nguyên văn;
   - những gì chưa làm được.

## Mức đạt K đề xuất (Tri quyết)

- Nhóm a, b, d, e: B ≥ A.
- Nhóm c: B chỉ được thua ở câu hỏi về hội thoại cũ hơn hội thoại gần nhất.
- Nhóm f: tầng 2 không đưa fact vào ở quá 1 probe.
- Nhóm g–j: đạt hết.
- Nhóm k: bản tóm tắt còn giữ lời dặn và fact của lượt 1 sau ít nhất 3 lần nén.
- Node memory: B không thêm lần gọi DB tuần tự nào so với A. p95 tăng không quá 50 ms. Số đo trên
  máy này nhiễu, vì mỗi lần gọi Neon mất khoảng 1 s.
- Lời gọi LLM chạy nền mỗi lượt của B không nhiều hơn A. Token được báo cáo.

## Giới hạn đã biết

- Điều agent nói ở hội thoại cũ hơn hội thoại gần nhất thì không tìm lại được.
- Fact chỉ hiện khi câu của user nói về đúng chủ đề đó. Fact ngầm như chiều cao không tự hiện.
- Xoá một hội thoại thì xoá các fact được ghi lần đầu từ hội thoại đó, kể cả khi user đã nhắc lại
  chúng ở hội thoại khác.
- Extractor chỉ thấy 60 dòng tầng 2 mới nhất, nên fact cũ hơn có thể bị thêm trùng.
- Câu purge chỉ chạy khi user chat. User không quay lại thì dòng đã ẩn và ứng viên quá hạn vẫn
  nằm trong DB. Cần một job quét định kỳ; việc này thuộc plan deploy.
- `gdpr.delete_message` vẫn lấy connection thẳng từ pool, không qua RLS. Lỗi có từ trước, plan này
  không sửa.

## Sau plan này (không làm ở đây)

1. Tri duyệt tính năng dựa trên benchmark.
2. Staging Lambda, đo trong vùng AWS:
   - RAM khi có model embed (đã ở 1.917/2.048 MB);
   - độ trễ của node memory;
   - thời gian khởi động lạnh;
   - chi phí Lambda;
   - phép thử model e5 int8.
3. Plan deploy:
   - tạo một Neon branch từ prod để làm bản sao lưu, rồi mới chạy migration 014, 015 và 016 trên
     prod (prod đang ở 013);
   - deploy tay CRUD Lambda;
   - thứ tự bật `VVA_LTM`;
   - job quét định kỳ xoá ứng viên quá hạn và dòng đã ẩn của mọi user;
   - ship theo 5 bước: commit → test → push feature → merge vào `release` local → push `release`.
     Không bao giờ dùng `push HEAD:release`.
4. Migration xoá bảng `summaries`, sau khi prod đã ổn.
