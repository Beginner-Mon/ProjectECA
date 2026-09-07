---
title: "Plan: Tách Google ra khỏi Email Signup (CreateAccountPage)"
status: draft
created: 2026-09-07
author: K (Senior Solution Architect)
tags: [auth, cognito, google, signup, plan]
related:
  - "[[ECA_UI/frontend/src/pages/CreateAccountPage]]"
  - "[[ECA_UI/frontend/src/pages/LoginPage]]"
  - "[[ECA_UI/frontend/src/pages/EnterPasswordPage]]"
  - "[[ECA_UI/frontend/src/lib/googleSignIn]]"
  - "[[ECA_UI/frontend/amplify/functions/pre-sign-up-handler/handler]]"
  - "[[docs/fixes/google-link-creates-wrong-account]]"
  - "[[docs/auth-google-incident]]"
---

# Plan: Loại bỏ "Continue with Google" khỏi route `/create-account`

> **Yêu cầu Owner (07-09-2026):** Route Google dư thừa ở chỗ khui vào signup lại có nút `Sign up with Google` để check email có đúng không. Bỏ tính năng đó. Khi đăng ký qua email Cognito thì chỉ có duy nhất 1 nút `Create Account`. Google và Email là 2 luồng tách riêng, làm 1 phát cả hai không cần thiết.

## 1. Context hiện tại

### 1.1 Flow hiện tại

```
[LoginPage] nhập email -> GET /api/lookup?email
  └─ !hasEmail && !hasGoogle -> navigate /create-account {email}
       └─ [CreateAccountPage] step=form
            ├─ (A) DisplayName + Password + Confirm -> signUp() -> verification -> confirmSignUp -> signIn -> "/"
            └─ (B) [DƯ THỪA] divider "or" + Continue with Google -> startGoogleSignIn(email) -> Hosted UI -> PreSignUp_ExternalProvider
                 -> createNativeAnchor(email) + linkGoogleTo() -> PostConfirmation -> AuthGuard check expectedEmail
```

* `CreateAccountPage.tsx:63-67` + `122-139`: `handleGoogleSignIn = () => startGoogleSignIn(email)` + nút Google + divider `or`.
* `LoginPage.tsx:94-95` đã điều hướng tới `/create-account` **chỉ khi lookup trả về `!hasEmail && !hasGoogle`** — tức đã biết chắc đây là địa chỉ mới, chưa có tài khoản nào. Việc cho phép rẽ sang Google tại đây tạo ra **luồng tranh chấp**: user vừa nhập password email, vừa có thể abandon sang federated sign-up cho cùng `email`. Nếu chọn nhầm Google account `b@` thay vì `a@`, `pre-sign-up-handler` (handler.ts:224-253) sẽ `createNativeAnchor(b@)` + link -> tạo tài khoản thật cho `b@` (incident `docs/fixes/google-link-creates-wrong-account.md:Bug B`).
* `googleSignIn.ts:55-67` và `AuthGuard.tsx:55-80` phải gánh thêm logic `expectedEmail` / `cognitoLogoutUrl` chỉ để catch mismatch này — vốn không cần nếu luồng signup là pure email.

### 1.2 Các nơi khác có Google — KHÔNG đụng tới

| Trang | Vai trò Google | Giữ hay bỏ | Lý do |
|---|---|---|---|
| `LoginPage.tsx:271-291` (cold `!result`) | Entry point chính cho Google sign-in khi chưa lookup | **GIỮ** | Là 1 trong 2 entry hợp lệ (cùng `showGoogleOnly` khi `!hasEmail && hasGoogle`) |
| `LoginPage.tsx:201-218` (`showGoogleOnly`) | `!hasEmail && hasGoogle` -> chỉ Google mới vào được | **GIỮ** | Đúng: account chỉ có Google |
| `LoginPage.tsx:221-252` (`showBoth`) | `hasEmail && hasGoogle` -> chọn Password hoặc Google | **GIỮ** | Đúng: account có cả 2 |
| `EnterPasswordPage.tsx:124-141` | `hasEmail` -> alternative Google cho cùng email | **GIỮ** (optional review) | Hợp lệ: login alternative, không phải signup. Nếu muốn strict hơn có thể để lại, không liên quan tới yêu cầu "bỏ ở signup". |
| `ProfileContent.tsx:85-107` + `googleLink.ts` | Linking sau khi đã đăng nhập (GIS `mountGoogleLinkButton`) | **GIỮ** | Luồng link đã fix Bug B, dùng `POST /api/user/link-google` với `AdminLinkProviderForUser` ONLY, không tạo account mới. Tách biệt hoàn toàn với `googleSignIn.ts`. |
| `CreateAccountPage.tsx:122-139` | Google trong lúc đang signup email | **BỎ** | Mục tiêu plan này |

## 2. Mục tiêu (Goals)

1. **Tách riêng 2 luồng signup:**
   - `Email signup` = `LoginPage --lookup--> CreateAccountPage --signUp/confirmSignUp/signIn--> /` — chỉ email/password + OTP.
   - `Google signup` = `LoginPage --Continue with Google--> Hosted UI (prompt=SELECT_ACCOUNT) --PreSignUp anchor--> /` — chỉ Google, không đi qua CreateAccountPage.
2. **CreateAccountPage chỉ có 1 CTA duy nhất:** `Create Account`. Không divider `or`, không nút Google, không import `startGoogleSignIn`.
3. **Không làm suy yếu defense hiện có:** `googleSignIn.ts` + `AuthGuard` vẫn giữ `expectedEmail` check cho các entry còn lại (`LoginPage`, `EnterPasswordPage`). Chỉ xóa call-site ở `CreateAccountPage`.
4. **Không đụng backend:** `pre-sign-up-handler`, `lookup-email`, `link-google`, `post-confirmation` giữ nguyên. PreSignUp vẫn cần để handle Google signup cold (tạo anchor khi `!hasEmail && !hasGoogle` mà user chọn Google ngay từ LoginPage).

## 3. Non-Goals

* Không xóa `lib/googleSignIn.ts`, không xóa `AuthGuard` mismatch logic.
* Không đổi `lookup-email` API hay `pre-sign-up-handler` — chúng phục vụ Google cold signup hợp lệ.
* Không bỏ Google ở `EnterPasswordPage` (trừ khi Owner confirm thêm).
* Không thêm feature "link cả hai trong 1 lần" ở signup — yêu cầu là tách, user muốn link thì làm sau trong Profile.

## 4. Thay đổi chi tiết

### 4.1 Frontend — `ECA_UI/frontend/src/pages/CreateAccountPage.tsx`

**Xóa:**
- `import { startGoogleSignIn } from '../lib/googleSignIn'` (line 4)
- `handleGoogleSignIn` (line 63-67) + comment `UI has committed...`
- JSX block `122-139`:
  ```tsx
  <div className="flex items-center gap-2"> <div className="h-px..."/> <span>or</span> ...</div>
  <button onClick={handleGoogleSignIn}> Continue with Google </button>
  ```

**Giữ nguyên:**
- `signUp` / `confirmSignUp` / `signIn` flow (line 25-61)
- `step === 'verification'` và `step === 'success'` (143-181)
- `useRedirectIfAuthenticated`, `AuthLayout`, `PasswordInput`

**Kết quả:** File giảm từ 184 -> ~145 dòng, 1 luồng duy nhất, không còn branching `or`.

### 4.2 Không đổi nhưng cần verify

* `LoginPage.tsx` — vẫn `navigate('/create-account', {state:{email}})` khi `!hasEmail && !hasGoogle` (line 94-95). Cold Google button `!result` (271-291) vẫn là entry cho Google signup — đây chính là "tách riêng" Owner muốn: muốn Google thì bấm ngay từ Login, không vào CreateAccount rồi mới bấm.
* `EnterPasswordPage.tsx` — giữ nguyên. Nếu Owner muốn strict "signup tách, login vẫn cho cả hai" thì để yên. Ghi chú trong plan để Owner quyết.
* `googleSignIn.ts` — giữ nguyên `prompt: 'SELECT_ACCOUNT'` unconditional + `expectedEmail` logic. Sau khi xóa call-site CreateAccount, `peekExpectedEmail` vẫn được set từ `LoginPage:115` và `EnterPasswordPage:76`.
* `AuthGuard.tsx` — giữ nguyên mismatch check (55-80). Không còn trigger từ CreateAccount nhưng vẫn bảo vệ 2 trang còn lại.

### 4.3 Tests

* Hiện **không có test nào cover nút Google ở CreateAccountPage** (`googleSignIn.test.ts` chỉ test lib, không test page). Xóa nút sẽ **không làm CI đỏ** — đây là gap đã ghi trong `docs/worklogs`.
* **Việc cần làm:**
  - Thêm 1 test mới `CreateAccountPage.test.tsx` (Vitest + React Testing Library) assert:
    - `step=form` render `Create Account` button, **không** render `Continue with Google`.
    - `signUp` được gọi với `username: email` + `preferred_username: displayName`.
  - Chạy `googleSignIn.test.ts` existing — phải vẫn pass (không đổi lib).
  - Manual QA checklist (mục 6).

### 4.4 Docs

* Cập nhật comment `CreateAccountPage.tsx:63-64` (xóa cùng code).
* Cập nhật `docs/fixes/google-link-creates-wrong-account.md` — thêm note: `CreateAccountPage Google branch removed 09-2026, cold Google signup now only via LoginPage`.
* Ghi worklog `docs/worklogs/DD-MM-YYYY.md` theo convention.

## 5. Plan thực thi (3 bước, 1 vertical slice)

### Step 1 — Code removal (15 phút)

1. Branch `feature/remove-google-from-signup` từ `feature/langgraph-rewrite` (hoặc `main` tùy Owner).
2. Edit `CreateAccountPage.tsx` như 4.1.
3. `npm run lint` + `npm run build` (check không còn unused import).
4. `grep -r "CreateAccountPage" --include="*.ts" --include="*.tsx"` đảm bảo không còn reference tới `startGoogleSignIn` trong file đó.

### Step 2 — Test & Verification (20 phút)

1. `vitest run src/lib/googleSignIn.test.ts` — pass.
2. Thêm `CreateAccountPage.test.tsx` (optional nhưng khuyến nghị) — chạy `vitest run`.
3. Manual QA (xem checklist mục 6) trên `amplify sandbox` hoặc `VITE_AUTH_DISABLED=false` local với `.env.local` trỏ sandbox pool.

### Step 3 — Review & Merge (10 phút)

1. PR mô tả: "tách Google khỏi email signup, CreateAccountPage pure email".
2. K (Mr. Senryuu) review worklog.
3. Merge, verify trên Amplify preview branch: cold Google signup vẫn work, email signup không còn nút Google.

## 6. QA Checklist (phải pass trước merge)

- [ ] `LoginPage` nhập email mới (`!hasEmail && !hasGoogle`) -> auto navigate `/create-account` -> chỉ thấy `Create Account`, không thấy Google.
- [ ] Hoàn thành email signup: `Create Account` -> nhận code -> `Verify` -> auto `signIn` -> redirect `/` -> `AuthGuard` không báo mismatch.
- [ ] `LoginPage` cold bấm `Continue with Google` (không nhập email) -> chooser hiện (`prompt=SELECT_ACCOUNT`) -> chọn account mới -> PreSignUp tạo anchor -> vào `/` thành công.
- [ ] `LoginPage` nhập email đã có cả 2 phương thức (`hasEmail && hasGoogle`) -> thấy `Sign in with Password` + `Continue with Google` (không đổi).
- [ ] `EnterPasswordPage` vẫn thấy `Continue with Google` (nếu quyết giữ).
- [ ] `Profile` -> `Link Google` (GIS button) vẫn work, `EMAIL_MISMATCH` 409 khi chọn sai email, không tạo account mới (verify `UserMappings` không có row mới cho `b@`).
- [ ] `AuthGuard` mismatch: `LoginPage` nhập `a@` -> `Continue with Google` -> chọn `b@` -> bị đá về `/login?error=email_mismatch` + `sessionStorage AUTH_ERROR_KEY`, Cognito cookie đã clear (không auto re-login).

## 7. Rủi ro & Mitigation

| Rủi ro | Mức | Mitigation |
|---|---|---|
| User quen bấm Google trong CreateAccount giờ không thấy | Thấp | LoginPage vẫn có cold Google entry rất rõ (`or` + button). Thêm hint text ở CreateAccount footer: "Already have Google account? Back to login" (optional). |
| PreSignUp vẫn tạo anchor cho Google cold signup — có tạo nhầm `b@` không? | Thấp | Không, đó là flow hợp lệ. Bug B cũ chỉ xảy ra khi **đã commit email `a@` mà lại sign-in `b@`** — đã loại bằng cách xóa branch đó. Cold Google không có `expectedEmail` nên không bị mismatch, nhưng AuthGuard không check (đúng). |
| Xóa nhầm Google ở EnterPasswordPage | Không | Plan này không đụng file đó. Nếu Owner muốn xóa luôn thì làm PR riêng. |
| Không có test bao phủ | Trung bình | Thêm `CreateAccountPage.test.tsx` như 4.3 để khóa regression. |

## 8. Rollback

Revert 1 commit duy nhất (xóa JSX + import). Không đụng backend nên không cần DB migration hay SSM change.

## 9. Quyết định cần Owner confirm

1. **EnterPasswordPage có giữ Google không?** Đề xuất: GIỮ (login alternative hợp lệ). Nếu Owner muốn pure tách luôn thì note để làm follow-up PR.
2. **Có thêm "Back to login" link ở CreateAccount không?** Đề xuất: có, để user lạc vào CreateAccount mà muốn Google thì quay lại LoginPage 1 click.

---

**File thay đổi duy nhất bắt buộc:** `ECA_UI/frontend/src/pages/CreateAccountPage.tsx` (xóa ~20 dòng). Tất cả file khác là verify/giữ nguyên.
