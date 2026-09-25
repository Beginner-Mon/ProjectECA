# ASVS remediation plan

Updated: 2026-09-17  
Baseline: `docs/security/asvs-5.0-checklist.csv` after PASS-evidence review and live release checks: 93 FAIL, 47 PARTIAL.  
Goal: close groups of related controls in vertical slices; do not try to implement every ASVS row independently.

## Current work

Active remediation batch: **Wave 2**. This handoff updates reporting and consolidates plans; Wave 2 implementation is next and has not yet been claimed complete.

The audit is finished. Wave 1's local implementation/test batch is handed off, with **213 frontend tests, 41 API/infra tests and the production build passing**. [Wave 1 report](../security/wave1-remediation-report.md) tracks the remaining release checks separately from missing implementation.

This is the single maintained ASVS plan. Completed audit instructions and the duplicate parallel plan have been retired; retain the CSV and audit/remediation reports as evidence.

## Approved owner decisions (2026-09-16)

- Domain/DNS migration is deferred to the final phase, after Waves 1–6. HSTS on current HTTPS hostnames can proceed independently; no preload submission now.
- CSP starts in report-only mode and moves to enforcement after compatibility evidence.
- Preserve Inter with production-quality self-hosting: licensed, version-pinned WOFF2 assets, Vietnamese/Latin support, font-display: swap, hashed caching and distributed attribution.
- All billing-specific changes are deferred to the final phase because billing is unfinished. This includes Stripe redirects, checkout/portal, webhooks, billing authorization, limits and tests.
- MFA remains a required system capability with optional user enrollment.

## Operating rules

1. One wave is one reviewable PR series. It may touch frontend, API, IaC, tests, and the security documentation needed to operate that control.
2. Every implementation changes the affected checklist rows only after its automated test and deployment/configuration evidence are available. `PASS` is never inferred from a planned change.
3. Do not ship an auth, TLS, or header change without a staging smoke test. Production console assertions are recorded as redacted evidence, never as screenshots or secrets committed to Git.
4. Work on one wave at a time by default. Parallel work is allowed only for workstreams with frozen interfaces and explicit file ownership; one integration owner updates the CSV after all streams pass review.
5. Code and tests do not authorize deployment. Staging/production deploys, AWS configuration changes, and DNS/domain changes require a separate go-ahead.

## Completed work and open Wave 1 follow-ups

Audit evidence is in [pass-evidence-review.md](../security/pass-evidence-review.md), [wave1-findings.md](../security/wave1-findings.md) and [wave2-findings.md](../security/wave2-findings.md). The last two files are historical **audit** reports, not implementation plans.

Wave 1 implemented headers on supported surfaces, report-only CSP, JSON response controls and self-hosted Inter. The detailed implementation instructions have been replaced by [wave1-remediation-report.md](../security/wave1-remediation-report.md) and the [response-header contract](../security/response-header-contract.md).

| Follow-up | Status and next action | Responsible work |
| --- | --- | --- |
| W1-D: deployment/browser evidence | Implemented paths need release-equivalent staging checks; production attestation later needs production evidence | Release verification; does not block independent Wave 2 work |
| W1-R1: gateway response coverage | Mock OPTIONS baseline is not implemented; inspect other managed response paths and add supported mappings before universal header claims | API/infra follow-up; serialize edits to gateway files with the Wave 2 integrator |
| W1-R2: CSP enforcement | Report-only is the approved first rollout; enforce only after compatibility evidence | Frontend/API follow-up after W1-D |
| W1-R3: CSP collector | Not implemented; a bounded collector, privacy/rate limits and ingestion tests are still required for reporting claims | Telemetry follow-up coordinated with Wave 5 |
| W1-R4: signed-media context protection | Restrictive CORP/Fetch-Metadata and asset rendering safeguards are not implemented/verified | Asset/frontend follow-up after media compatibility checks |
| W1-R5: external resources | Inter is local; GSI and optional HDR need a reviewed exception or further integrity/self-hosting work | Resource review; see external-resource-inventory.md |

These follow-ups remain open and retain their checklist verdicts. Advancing to Wave 2 does not mean all Wave 1 controls PASS. Domain migration/preload and all billing-specific changes remain in the final phase.

## Wave 2 — Active: abuse controls, validation and authorization

**Why now:** these controls make the public API sustainable before strengthening higher-cost identity flows; the documentation becomes the testable contract for future endpoints.

**Owns:**

- Primary documentation controls: V2.1.1 and V8.1.1. API abuse controls cover V2.4.1/V2.4.2; V6.1.1/V6.3.1 authentication-specific work is owned by Wave 3.
- Related controls: V1.2.2, V1.3.3, V1.3.6, V2.1.2, V2.1.3, V2.2.1, V2.2.3, V8.1.2–V8.1.4, V15.3.4, V15.3.7. Existing PASS evidence is preserved; status changes require an actual control test.

**Implementation slices:**

1. Publish a concise validation-and-limits contract: every external field, type, maximum length/range, coupled-field rule, and per-user/global cost limit. Generate or test schemas against it where practical.
2. Add a distributed, identity-keyed rate limiter for chat, search and expensive motion/TTS routes, with bounded queue/concurrency limits. Auth-provider login/reset/OTP controls belong to Wave 3; billing-specific limits are deferred to the final phase. Do not trust a raw forwarded IP header without a configured trusted proxy.
3. Publish authorization rules mapping route/action/field to principal, ownership/RLS rule, and expected denial. Include an explicit “no contextual rule is used” statement where applicable.
4. Define duplicate-query-parameter behavior and reject ambiguous parameters before handlers consume them.
5. Centralize application-side URL validation for external fetch call sites: exact supported destinations, scheme/port/user-info rules, redirect handling and private-address/DNS-rebinding defenses where the client permits them. Distinguish user-controlled URLs from operator-configured services and explicit local development endpoints. Network-level egress belongs to Wave 4.

**Execution order and file ownership:**

| Task | Work and owned files | Dependency |
| --- | --- | --- |
| W2-A: validation and bounds | `api/schemas.py`, a validation contract and focused schema tests; document/implement query, session ID, token and expensive-input bounds | First implementation task; preserve existing clients and make changed limits explicit |
| W2-B: authorization contract | Authorization matrix and dedicated ownership/denial tests; read existing `api/auth.py`, CRUD/preferences routes and RLS | Can run beside A with exclusive documentation/test files; route mutations go through integrator |
| W2-C: outbound URL checks | `tools/youtube_ingest.py`, `mcp/web_search_server.py`, configured TTS client and URL-policy tests | After destination rules are fixed; no billing URLs or infrastructure egress edits |
| W2-D: API abuse/ambiguous requests | Limiter module, concurrency/duplicate-parameter tests; integrator owns app/route wiring and any schema overlap | Consume A's contract; choose shared atomic storage and failure/recovery behavior before implementation; process-local counters alone are insufficient across Lambda instances |
| W2-I: integration/evidence | Shared route/app files, combined tests, report and CSV | One writer; integrate after each bounded slice |

No blanket parallel editing of `main.py`, `crud_app.py`, `schemas.py`, shared test configuration, lockfiles or gateway stacks. An owner needing another task's file routes the change through W2-I.

**Material changes to call out:** new rejection limits, rate-limit thresholds, storage/operating costs, false-positive URL blocking and queue behavior. Preserve billing deferral and the optional user-MFA decision. Do not silently activate paid infrastructure or change authentication policy.

**How to verify:** deterministic unit tests with a fake clock/store, concurrency tests, two-user authorization tests, duplicate-parameter tests, and staging 429/backoff tests that do not lock a real user.

**Exit:** rate-limit keys cannot be user-forged; documentation references executable tests; each affected API has an ownership/field rule.

## Wave 3 — Identity, OAuth, password, and session lifecycle

**Why now:** this has product and Cognito decisions, and changes in it must be atomic across the SPA, Cognito configuration, backend JWT verification, and account-deletion path.

**Owns:**

- P0: V6.1.1, V6.3.1, V6.2.3–V6.2.5, V7.4.2. Authentication documentation/abuse controls V6.1.2–V6.1.3 are also owned here.
- P1 identity/session/OAuth: V6.2.11–V6.2.12, V6.3.3, V6.3.5, V6.3.7–V6.3.8, V6.6.3, V6.8.4, V7.1.1–V7.1.3, V7.3.1, V7.4.3, V7.4.5, V7.5.1–V7.5.3, V7.6.1, V10.3.1–V10.3.2, V10.5.1.

**Implementation slices:**

1. Pin Cognito password policy in IaC, rather than relying on console defaults. Add breached-password/common-password protection and document allowed composition. Implement a real change-password flow that requires the current password.
2. Configure or implement rate limiting and user-enumeration-safe behavior for login, reset, and OTP. Implement MFA capability with optional user enrollment under the approved policy; define secure enrollment, factor changes and recovery without making MFA mandatory for every user.
3. Replace use of an ID token as an API access token with an access-token resource-server contract: expected audience/client, scopes, and backend authorization checks. Add OIDC nonce generation with Web Crypto, short-lived storage tied to OAuth state, claim comparison after callback, and cleanup on success/failure.
4. Define session lifetime/concurrency and re-authentication policy. Implement token revocation/`valid-after` or denylist semantics, then wire deletion/disable, password-factor change, “terminate other sessions,” and admin termination to it.
5. Add session inventory/termination UI only after the server-side revocation model exists.

**How to verify:** isolated Cognito configuration assertions, JWT fixture tests (wrong `aud`, scope, nonce, old/revoked token), browser OAuth callback tests, two-device session tests, delete-user teardown test, and audit logs for sensitive factor/session events.

**Exit:** account deletion invalidates Cognito and application access; a captured/replayed OAuth result fails nonce validation; no session-lifecycle UI promises behavior the backend cannot enforce.

## Wave 4 — Transport, data, secrets, and dependency risk

**Why now:** infrastructure changes require deployment validation and should not be mixed with identity behavior changes.

**Owns:**

- P0: V12.1.1, V15.1.1, V15.2.1.
- Related fails/partials: V12.3.2, V12.3.4, V13.2.1, V13.2.4–V13.2.5, V13.3.3–V13.3.4, V14.1.1–V14.1.2, V14.2.4–V14.2.5, V14.3.2, V15.1.2–V15.1.5, V15.2.2, V15.2.4.

**Implementation slices:**

1. Reverify existing endpoint TLS and record any unresolved policy gap; custom-domain migration is deferred to the final phase. Use an explicit verified SSL context (`CERT_REQUIRED`, hostname verification) for PostgreSQL and migration clients.
2. Add API `Cache-Control: no-store` for authenticated/sensitive responses and test it; document data classes, storage locations, retention, and protection requirements.
3. Replace broad network egress with destination-specific rules where the platform supports it; make application URL allowlists consistent with those rules. Rotate/migrate long-lived secrets according to a documented schedule; isolate signing keys or document a bounded risk acceptance/HSM path.
4. Publish dependency policy/SLA and risky/dangerous-component registers. Generate CycloneDX SBOM in CI; triage, upgrade, replace, or formally accept every high/moderate vulnerability within the SLA.

**How to verify:** CDK synth/template inspection, staging TLS scan, a certificate-hostname failure test, authenticated cache-header tests, egress-denial tests, secret-access IAM policy tests, SBOM artifact, and clean/accepted vulnerability reports.

**Exit:** no implicit certificate validation setting; public TLS evidence is captured from deployed domains; every dependency finding has an owner, due date, and disposition.

## Wave 5 — Operational design, logging, and production exposure

**Why now:** reliable logging and documented operational limits make the prior controls supportable in production.

**Owns:**

- V13.1.2–V13.1.4, V13.2.6, V13.4.5.
- V16.1.1, V16.2.3, V16.3.2, V16.4.2–V16.4.3, V16.5.4.
- Supporting P1/P2 records on service limits, rotation, health/docs exposure, and error handling.

**Implementation slices:**

1. Write backend communication/resource-management and secret-rotation runbooks: timeout, retry, pool exhaustion, queue behavior, ownership, and recovery.
2. Make production `/docs`, `/openapi.json`, detailed health, debug endpoints, and version detail private or disabled; retain only an authenticated/readiness-safe health endpoint.
3. Publish log inventory and event taxonomy. Log authentication, authorization denial, rate-limit, session-revocation, TLS/breaker, and high-risk administration events without tokens, password, query/PII, or raw exceptions.
4. Add a last-resort exception handler with stable public error codes and request IDs. Ship immutable/restricted logs to a separate security account/SIEM, with retention and access policy.

**How to verify:** production-mode route tests, redaction tests, forced-unhandled-error tests, log-schema tests, IAM/access-policy inspection, and an end-to-end correlation drill using only synthetic data.

**Exit:** production attack-surface scan cannot reach developer/debug documentation; an incident can be investigated from one request ID without exposing sensitive data.

## Wave 6 — L3/advanced controls and documented risk decisions

**Why last:** these need mature operating controls and may require a product/business decision rather than code alone.

**Owns:** remaining P2 failures, including browser-support behavior (V3.1.1, V3.7.5), HSTS preload assessment (V3.7.4; implementation deferred to the final domain phase), adaptive authorization (V8.2.4), DPoP/mTLS (V10.3.5), crypto inventory/PQC/HSM posture (V11.1.1–V11.1.4, V11.7.1), ECH (V12.1.5), and advanced session/admin experience.

**Implementation approach:** classify each as one of: implement now, platform-constrained with bounded risk acceptance, or out of scope for the declared ASVS level. A risk acceptance names the owner, affected data, compensating controls, expiry date, and re-review trigger.

**How to verify:** architecture review plus a targeted control test for anything implemented; do not mark an unsupported platform feature PASS merely because its risk is accepted.

## Final phase — Domains and billing (owner-deferred)

Start after Waves 1–6. Domain work includes certificates, DNS, API TLS policy, CORS/CSP origins, frontend environment, OAuth callback/logout URLs, HSTS and any eligible preload decision. Billing work includes the deferred V3.7.2 redirect allowlist, checkout/portal safety, webhook binding/replay/idempotency, authorization and rate limits after its feature contract is stable.

Preparation can be independent, but integrate against frozen final origins and test OAuth, Stripe return URLs, session behavior, streaming and assets together. Keep all unresolved findings open until verified; deferral is not risk acceptance. Domain/DNS changes and deployment remain a separate release step.

## Release and re-audit cadence

For every wave: implement → automated tests → staging deploy and smoke tests → collect non-secret evidence → update only its ASVS rows → security review → release. Re-run the PASS-evidence review after Waves 3 and 5 because they alter authentication, transport, and deployed infrastructure assumptions.

The expected order is Wave 1 → Wave 2 → Wave 3 → Wave 4 → Wave 5 → Wave 6 → final domains/billing phase. Only isolated Wave 4 DB TLS and dependency work may overlap Wave 3 after exact file ownership is assigned. Use the file-ownership rules above. Wave 3 authentication implementation stays coordinated; Wave 5 documentation can be prepared in parallel but runtime logging/exposure changes must share one owner for main.py. Wave 6 independent research does not authorize concurrent production changes.
