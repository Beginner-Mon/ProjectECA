# Plan: Rename VVA → ECA, tầng A (chỉ những tên sống trong repo)

> Người thực thi: subagent của N. Người duyệt: N.
> Base: `feature/langgraph-rewrite` tại `1d13b3ee` (đang khớp `origin`). Số dòng bên dưới đúng tại commit này; nếu lệch thì dò theo nội dung, không theo số dòng.

## Context

Dự án đổi tên từ VVA sang ECA. Repo GitHub đã là `ProjectECA`, folder UI đã là `ECA_UI`, nhưng trong code vẫn còn "VVA" ở hai dạng rất khác nhau:

- **Tên gọi của dự án** (title, docstring, nhãn nội bộ, key lưu trên trình duyệt). Đổi không kéo theo thay đổi nào ngoài git. Đây là **tầng A**, phạm vi của plan này.
- **Tên định danh của thứ có thật** (env var `VVA_*`, SSM `/vva/*`, Lambda/ECR `vva-agent`, stack `Vva*Stack`, package `vva_motion`, bảng DynamoDB, metadata Stripe). Đây là tầng B, **nằm ngoài plan này**.

Commit `c1f27f80` (01/09) đã chạy một lượt find-replace mù, để lại ba chỗ sai mà plan này sửa luôn: comment nói code đọc một env var không tồn tại, DSN local trong code lệch với `docker-compose`, và file `eca-rename-plan.md` bị hỏng nội dung.

Kết quả mong muốn: không còn "VVA" nào là tên dự án trong code ứng dụng; người dùng đang dùng không mất phiên chat hay thiết lập; không có thay đổi nào ở AWS, `.env`, database.

## Quy tắc cứng

1. **Không find-replace hàng loạt.** Chỉ sửa đúng các dòng liệt kê bên dưới. Gặp chỗ "VVA" ngoài danh sách thì để nguyên và ghi vào báo cáo.
2. **Không đụng** (tầng B hoặc lịch sử):
   - mọi `VVA_*` env var, `/vva/*`, `vva-agent`, `vva-speechllm`, `vva-crud`, `vva-characters`, `vva-stm`, `vva-motion-jobs`, `Vva*Stack`, `vva_admin`;
   - package `vva_motion` và mọi `import vva_motion`;
   - `vva_user_id` trong [billing.py](agenticRAG/langgraph_agents/api/billing.py) và `007_rls.py` (metadata Stripe đã phát hành);
   - `vva-billing-sandbox.sqlite3`, `vva-local:sandbox` trong `billing_local.py` (đổi seed là đổi id người dùng sandbox);
   - `infra/lambda/layer/shared/*` (đổi nội dung file làm đổi hash asset → phát hành LayerVersion mới);
   - mọi chuỗi `description=` / `comment=` trong `infra/infra/*.py` (đổi template CloudFormation);
   - `docs/worklogs/**`, `docs/archive/**`, `infra/cdk.out/**`, `infra/spike/**`;
   - [start_services.py:81](start_services.py#L81) (chữ `"ECA"` ở đó là tên conda env thật, không phải lỗi).
3. **Git:** chỉ `git add <đường dẫn cụ thể>`, không `git add -A` / `git add .`. Cây làm việc đang có `.claude/CLAUDE.md` (đã sửa) và `docs/architecture/aws-layered.drawio` (chưa track) của N — không stage, không restore, không stash.
4. Không chạy `npm install`. Nếu thiếu `node_modules` thì dùng `npm ci`.

## Bước 0 — Nhánh và baseline

```
git checkout -b chore/rename-tier-a feature/langgraph-rewrite
```

Chạy và lưu kết quả **trước khi sửa** (để so sánh, vì repo có sẵn một số test đỏ):

```
cd ECA_UI/frontend && npm test && npm run build && npm run lint
C:\Miniconda\envs\firstconda\python.exe -m pytest tests/langgraph_agents tests/infra -q -W ignore::PendingDeprecationWarning
```

## Commit 1 — Tên dự án và nhãn nội bộ (backend + infra docstring)

Thay "VVA" bằng "ECA" trong đúng các dòng sau, không đổi gì khác trên dòng:

| File | Dòng | Loại |
|------|------|------|
| [agenticRAG/langgraph_agents/api/main.py](agenticRAG/langgraph_agents/api/main.py) | 229 | `title="ECA LangGraph v2.5"` |
| [agenticRAG/langgraph_agents/api/crud_app.py](agenticRAG/langgraph_agents/api/crud_app.py) | 93 | `title="ECA CRUD API"` |
| [agenticRAG/langgraph_agents/api/billing_local.py](agenticRAG/langgraph_agents/api/billing_local.py) | 3, 203 | docstring; `title="ECA billing sandbox (local only)"` |
| [agenticRAG/langgraph_agents/state.py](agenticRAG/langgraph_agents/state.py) | 1 | docstring |
| [agenticRAG/langgraph_agents/alembic/env.py](agenticRAG/langgraph_agents/alembic/env.py) | 1 | docstring |
| [agenticRAG/.env.example](agenticRAG/.env.example) | 1 | comment đầu file |
| [agenticRAG/langgraph_agents/db/postgres.py](agenticRAG/langgraph_agents/db/postgres.py) | 36, 61 | tên ContextVar → `"eca_request_user_id"`, `"eca_request_character"` |
| [infra/app.py](infra/app.py) | 2 | docstring |
| [infra/README.md](infra/README.md) | 1 | tiêu đề |
| [infra/infra/api_gateway_stack.py](infra/infra/api_gateway_stack.py), [lambda_stack.py](infra/infra/lambda_stack.py), [rest_api_stack.py](infra/infra/rest_api_stack.py) | 1 | **chỉ** docstring dòng 1 |

Kiểm tra sau khi sửa: `git diff -- infra/` chỉ được chứa dòng docstring hoặc tiêu đề markdown; không dòng nào có `description=`, `comment=` hay một chuỗi nằm trong lời gọi construct.

## Commit 2 — Frontend: nhãn và key lưu trên trình duyệt

**2a. Nhãn** (thay trực tiếp):

- [ECA_UI/frontend/.env.example:1](ECA_UI/frontend/.env.example#L1), [src/lib/api.ts:2](ECA_UI/frontend/src/lib/api.ts#L2): comment "VVA" → "ECA".
- [vite.config.ts:56](ECA_UI/frontend/vite.config.ts#L56): `'eca-require-build-env'`.

**2b. Đổi key**, theo quy ước gạch nối đã có sẵn (`eca-theme`, `eca-locale`):

| File | Key cũ | Key mới |
|------|--------|---------|
| [src/lib/chatSession.ts:53](ECA_UI/frontend/src/lib/chatSession.ts#L53) | `vva_session_id` | `eca-session-id` |
| [src/contexts/AvatarBgContext.tsx:6](ECA_UI/frontend/src/contexts/AvatarBgContext.tsx#L6) | `vva_avatar_bg` | `eca-avatar-bg` |
| [src/contexts/GraphicsContext.tsx:19](ECA_UI/frontend/src/contexts/GraphicsContext.tsx#L19) | `vva-graphics-settings` | `eca-graphics-settings` |
| [src/lib/api.ts:168](ECA_UI/frontend/src/lib/api.ts#L168) | `vva_demo_user` | `eca-demo-user` |
| [src/lib/ttsCache.ts:212](ECA_UI/frontend/src/lib/ttsCache.ts#L212) | `vva-tts-cache` (IndexedDB) | `eca-tts-cache` |

**2c. Chuyển dữ liệu cũ** — file mới `ECA_UI/frontend/src/lib/storageKeyMigration.ts`:

- Export `migrateStorageKeys(storage: Storage)`: với mỗi cặp trong 4 key localStorage ở trên, nếu key cũ có giá trị thì chép sang key mới **khi key mới chưa có**, rồi xoá key cũ. Bọc `try/catch` cho từng cặp (localStorage có thể ném lỗi ở chế độ riêng tư). Chạy lần hai không đổi gì.
- Export `dropOldTtsCache()`: gọi `indexedDB.deleteDatabase('vva-tts-cache')` trong `try/catch`, không chờ kết quả. Không chép dữ liệu: đây là cache có TTL, mất thì tải lại.
- Cuối module, tự chạy cả hai, có chặn môi trường: `if (typeof localStorage !== 'undefined')` và `if (typeof indexedDB !== 'undefined')` (vitest ở repo này chạy môi trường `node`).
- Comment đầu file ghi ngày thêm và ngày được phép gỡ bỏ: đề xuất 06/11/2026 (một tháng). Ai không mở lại app trong khoảng đó sẽ về thiết lập mặc định và bắt đầu phiên chat mới; không mất dữ liệu phía server.

Trong [src/main.tsx](ECA_UI/frontend/src/main.tsx), thêm `import './lib/storageKeyMigration'` làm **dòng import đầu tiên**, kèm comment lý do: [GraphicsContext.tsx:45](ECA_UI/frontend/src/contexts/GraphicsContext.tsx#L45) đọc localStorage ngay khi module được nạp (`let state = load()`), nên việc chuyển key phải chạy trước mọi import khác, không phải trong thân `main.tsx`.

**2d. Test** — file mới `src/lib/storageKeyMigration.test.ts`, dùng kiểu `fakeStorage` như trong [chatSession.test.ts:23](ECA_UI/frontend/src/lib/chatSession.test.ts#L23) (chép helper, không sửa file test cũ). Bốn ca: chép cũ sang mới và xoá cũ; không ghi đè khi key mới đã có; không làm gì khi không có key cũ; storage ném lỗi thì không văng exception.

## Commit 3 — Sửa ba chỗ hỏng do `c1f27f80`

**3a. Comment nói sai.** Không dòng code nào đọc `ECA_PG_DSN_PARAM`; [postgres.py:108](agenticRAG/langgraph_agents/db/postgres.py#L108) chỉ đọc `VVA_PG_DSN_PARAM`.

- [requirements-langgraph.txt:34-35](requirements-langgraph.txt#L34): đổi `ECA_PG_DSN_PARAM` về `VVA_PG_DSN_PARAM`, xoá dòng `(legacy VVA_PG_DSN_PARAM still accepted with warning until S6)`.
- [agenticRAG/requirements-agent-runtime.txt:57](agenticRAG/requirements-agent-runtime.txt#L57): bỏ `ECA_PG_DSN_PARAM (legacy VVA_PG_DSN_PARAM compat)`, viết lại là `VVA_PG_DSN_PARAM`.

**3b. DSN local lệch nhau.** `docker-compose.langgraph.yml`, `config/langgraph.yaml`, `alembic.ini` và runbook đều là `eca:eca_dev@localhost:5433/eca`; phần fallback trong code vẫn là `vva:vva_dev@…/vva` nên không kết nối được vào container tạo mới. Đổi về `postgresql://eca:eca_dev@localhost:5433/eca` tại:

- [postgres.py:21](agenticRAG/langgraph_agents/db/postgres.py#L21) (`_LOCAL_DSN`), [alembic/env.py:48,51](agenticRAG/langgraph_agents/alembic/env.py#L48);
- bốn test đang hardcode: `tests/langgraph_agents/test_a1_a2_a3.py:25`, `test_memory_regression.py:25`, `test_motion_job_id_write_path.py:30`, `test_pr1_memory_fix.py:17`;
- ba chỗ tài liệu: [agenticRAG/.env.example:36](agenticRAG/.env.example#L36), [scripts/QUICKSTART.md:151](scripts/QUICKSTART.md#L151), [docs/ops/neon-migration-us-east-1.md:135](docs/ops/neon-migration-us-east-1.md#L135).

Phần này chỉ ảnh hưởng máy chạy Postgres bằng Docker local và không đặt `VVA_PG_DSN`. Volume Docker tạo trước 01/09 mang user `vva` sẽ phải tạo lại (`docker compose down -v`). **N có thể gạch bỏ 3b nếu muốn giữ tầng A thuần đổi tên**; khi đó chỉ sửa ba chỗ tài liệu cho khớp với code hiện tại (`vva:vva_dev@…/vva`).

**3c. Plan cũ.** Thêm vào đầu [docs/plans/eca-rename-plan.md](docs/plans/eca-rename-plan.md) một khối ghi chú: nội dung đã bị find-replace làm hỏng từ `c1f27f80` (bảng mapping thành "ECA → ECA"), không thực thi; tầng A đã làm theo `.claude/plans/rename-tier-a.md`; tầng B cần plan mới. Không sửa phần thân.

## Commit 4 — Worklog và plan

Commit kèm chính file plan này (`.claude/plans/rename-tier-a.md`, hiện chưa track). Thêm mục vào `docs/worklogs/<ngày thực thi DD-MM-YYYY>.md` (tạo mới nếu chưa có, theo frontmatter của file worklog gần nhất): liệt kê ba commit trên, danh sách key đã đổi, và ngày dự kiến gỡ `storageKeyMigration.ts`.

## Verification

1. Chạy lại đúng hai lệnh ở bước 0. Điều kiện đạt: `npm run build` và `npm run lint` sạch, bốn test mới xanh, và **không có test nào đỏ thêm** so với baseline. Nếu có, dừng và báo cáo, không sửa test để cho qua.
2. Grep xác nhận phạm vi:
   - `rg -n "vva" ECA_UI/frontend/src ECA_UI/frontend/vite.config.ts` → chỉ còn trong `storageKeyMigration.ts` và file test của nó.
   - `rg --pcre2 -n '(?<![A-Za-z_/-])VVA(?![A-Za-z_-])' -g '!infra/cdk.out/**' -g '!docs/**' -g '!infra/spike/**'` → chỉ còn 10 dòng được phép: 7 dòng `description=`/`comment=` trong `infra/infra/*.py` và 3 file trong `infra/lambda/layer/shared/`.
   - `rg -n "ECA_PG_DSN_PARAM|vva:eca_dev" -g '!docs/plans/**' -g '!docs/worklogs/**'` → không còn kết quả nào.
3. Chạy thử trên trình duyệt (`npm run dev`): trước khi nạp bản mới, đặt tay `localStorage.setItem('vva-graphics-settings', '{"mtoon":false}')` và một `vva_avatar_bg` hợp lệ; nạp lại trang; xác nhận thiết lập được giữ, key `eca-*` xuất hiện, key `vva*` biến mất, và DevTools → Application → IndexedDB không còn `vva-tts-cache`.
4. `GET /openapi.json` của backend local trả `"title": "ECA LangGraph v2.5"`.

## Ship — năm bước, không gộp

```
1. commit trên chore/rename-tier-a                       (đã làm ở trên)
2. test local                                            (mục Verification)
3. git checkout feature/langgraph-rewrite
   git merge --ff-only chore/rename-tier-a
   git push origin feature/langgraph-rewrite             ← dừng ở đây, báo cáo cho N
4. git fetch origin
   git checkout release && git merge --ff-only origin/release
   git merge feature/langgraph-rewrite
5. git push origin release                               ← chỉ lệnh này kích hoạt deploy
```

- **Dừng sau bước 3.** Bước 4 và 5 chỉ làm khi N nói rõ. Trước bước 4, báo cho N kết quả `git log --oneline origin/release..feature/langgraph-rewrite`, vì merge sẽ đẩy luôn mọi commit chưa phát hành khác trên nhánh feature.
- **Cấm `git push origin HEAD:release`.**
- Không commit, không sửa gì trực tiếp trên `release`.

## Báo cáo cuối

Subagent trả về: hash của bốn commit; kết quả test trước và sau; ba kết quả grep ở mục Verification; danh sách mọi chỗ "VVA" gặp phải ngoài danh sách và đã để nguyên; bước ship đã dừng ở đâu.
