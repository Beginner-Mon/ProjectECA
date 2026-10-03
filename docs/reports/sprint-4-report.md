---
tags: [report, sprint-4, draft]
---
# Sprint Four Report — draft for team review

- **Unit:** COS40006 / EAT40006 Computing Engineering Technology Project B — Portfolio task 5
- **Project:** ECA (Embodied Conversational Agent)
- **Team:** Tri Tran, Nguyen, Tony Lee
- **Reporting window:** 25/04/2026 – 03/10/2026. This report continues the Sprint 3 report. It covers the work carried over the break (May to mid-September) and Sprint 4 itself (weeks 1–3, 18/09 – 09/10).
- **Submission date:** `[TEAM]`
- **Figures:** numbers refer to the Figure list in [[sprint-4-evidence-pack]].

Text in `[TEAM]` or `[…]` is still to be filled in by a person. Story points marked *(proposed)* must be confirmed by the owner of the item.

## Open items before this goes into Word

| # | Item | Who | Section |
|---|---|---|---|
| 1 | Semester 1 risk register, so old risk IDs are kept | Tri provides the file | 5 |
| 2 | Sprint 3 report, to reuse backlog IDs and fill the Sprint 3 row | Tri provides the file | 4.3, 7.3 |
| 3 | Notes from the client demo: date, attendees, what was shown, feedback | Tri provides the notes | 7.1, 7.2, 7.5 |
| 4 | Screenshot of the Jira board (Figure 4) and the other figures | Team | 6 |
| 5 | Scrum master and roles for this sprint | Team | 4.2 |
| 6 | Confirm every story point value and the acceptance criteria marked `[owner]` | Each owner | 4.4 |
| 7 | Contribution summary table | Team, together | 2 |
| 8 | Acknowledgment of Country | Each member | 1 |
| 9 | Own contribution statement, written from the evidence pack | Nguyen, Tony Lee | 3.2, 3.3 |
| 10 | Voice dictation: Tri's Week 2 worklog lists it, but the code is inside commit `c89f9295` authored by Tony Lee. Agree who reports it | Tri, Tony Lee | 3, 6.2 |
| 11 | Many commits carry a `Co-Authored-By: Claude` line that is visible on GitHub. Agree how the use of AI tools is declared under the unit rules | Team | 8.4 |
| 12 | The security audit branch `owasp-check` exists on one laptop only. Push it to GitHub, or the evidence for AUTH-07 cannot be checked | Tri | 6.2 |
| 13 | Root causes marked *(to confirm)* | Team | 8.2 |

---

## 1. Acknowledgment of Country

`[TEAM: acknowledgment statement]`

| Member | Location while completing this work | Traditional Owners of that land (if living in Australia) |
|---|---|---|
| Tri Tran | `[TEAM]` | `[TEAM]` |
| Nguyen | `[TEAM]` | `[TEAM]` |
| Tony Lee | `[TEAM]` | `[TEAM]` |

## 2. Contribution summary (this sprint)

To be completed by all members together.

| Team member | Finished tasks on time with acceptable quality | Self-assigned tasks and found solutions | Attended all team meetings | Attended all supervisor meetings | Attended all client meetings | Replied within an acceptable time | Kept the team updated | Respected others and listened |
|---|---|---|---|---|---|---|---|---|
| Tri Tran | | | | | | | | |
| Nguyen | | | | | | | | |
| Tony Lee | | | | | | | | |

## 3. Contribution details (this sprint)

### 3.1 Tri Tran (104993926)

In this period I built the sign-in system and the security around user data, created the new web frontend, and moved the 3D motion service and the AI agent onto AWS. In Sprint 4 itself I made the avatar speak on the live website, redesigned the chat for mobile phones, added thumbs up/down on each answer, and improved how the agent chooses its tools and quotes exercise doses. I recorded 90 hours in my weekly worklogs for weeks 1 and 2 (45 h each). My GitHub history shows 406 commits in the reporting window (merges excluded, duplicates removed).

| Epic / area | What I delivered | Evidence |
|---|---|---|
| Auth | Email sign-up and sign-in with protected pages. Google sign-in and account linking. Removed the switch that could turn sign-in off, so the user's identity now comes only from the login token. Row-level security on the six user tables, with a separate database role for the app. One protected entry point for the API with request limits. A security audit against all 345 OWASP ASVS 5.0 requirements. | `08e8e4f3`, `dafb4bd0`, `8cb1bcc6`, `e2af651f`, `634c47ee`, `d4781fec`, `21f8f1dc`, `3fd6ea27`; worklog `18-08-2026.md`; branch `owasp-check`; Figures 5, 6, 7, 18 |
| Frontend | New React 19 + Vite app hosted on Amplify. Avatar animation: no more T-pose flash, Mixamo retargeting, gesture dispatcher, smoother blending between animations. Character list, avatar background picker, preferences that follow the user across devices. Website language (English / Vietnamese) separate from the assistant's reply language. Mobile chat dock. Thumbs up/down with a reason form. Lint backlog cleared. | `50d87588`, `d948019f`, `1e2448d8`, `6f4c17f2`, `eb484f9d`, `fa8cd83e`, `484bb327`, `32bb9c03`, `cd7816bf`, `ef2faff7`, `50593a0b`, `53b3ff73`, `ee71f4a6`; Figures 8, 9, 10 |
| Kimodo motion | Production design on a GPU server (g5.xlarge) and its container pipeline. Converter from Kimodo output to the BVH format the avatar plays. A job queue, a GPU worker with a heartbeat, and short-lived signed download links. Deployed and tested end to end: 7 seconds from request to finished motion. The avatar plays the generated motion and keeps it after a refresh. | `ec62f1b0`, `36f94493`, `659051de`, `c7bf7e4d`, `07a24ca1`, `189fc49f`, `bf00cd2e`, `4d28a612`, `c4f3faf4`, `1cdde23c`, `5eb08461`, `8b3b26c0`; worklog `28-08-2026.md`; Figures 11, 12 |
| LangGraph agent | Hosted the agent on AWS Lambda (container image, infrastructure code, automated deployment, frontend switch-over). Agent-context rounds 1–3: the agent picks tools from their descriptions, quotes an exercise dose only when the evidence states it, and reads personas from files. A repeatable probe to measure each change, run eleven times (V0 to V7; 51 questions in the last run). | `069bb501`, `59d6bc6e`, `68963211`, `39437717`, `2b54aec8`, `654e8064`, `1a0f62d3`; worklogs `01-10-2026.md`, `02-10-2026.md`, `02-10-2026-round3.md`; Figures 13, 14 |
| Platform & voice | Vietnamese text-to-speech streamed end to end, live on the website since 23/09. Found why the voice was choppy on the live site (slow network route) and fixed it by sending audio through the CDN. Fixed the agent running out of memory in production. Database table and API for message feedback. | `885ec019`, `818487d1`, `132825e1`, `0fb60503`, `594437ab`, `dc6ce970`, `161badcd`, `c7524390`; weekly worklogs weeks 1–2; worklog `14-09-2026.md` |

### 3.2 Nguyen

`[To be written by Nguyen — see evidence pack, section 3]`

### 3.3 Tony Lee

`[To be written by Tony Lee — see evidence pack, section 4]`

## 4. Sprint plan

### 4.1 Sprint goal

*(proposed — team to confirm)* By the end of week 3, a real user can use the deployed assistant from start to finish on a computer or a phone: sign in, ask about an exercise, hear the answer spoken, watch the avatar show the movement, and rate the answer. Every exercise dose in an answer comes from the knowledge base.

### 4.2 Roles

| Role | Member | Responsibility this sprint |
|---|---|---|
| Scrum master | `[TEAM]` | `[TEAM]` |
| Product owner contact | `[TEAM]` | `[TEAM]` |
| Developer | Tri Tran | AWS infrastructure and deployment, frontend, motion pipeline, agent context |
| Developer | Nguyen | `[Nguyen to confirm]` |
| Developer | Tony Lee | `[Tony Lee to confirm]` |

### 4.3 Sprint roadmap

| Sprint | Dates | Goal | Output that feeds the next sprint |
|---|---|---|---|
| Sprint 3 | Semester 1, weeks 11–13 | `[from Sprint 3 report]` | `[from Sprint 3 report]` |
| Carry-over | May – mid-September 2026 | Rebuild the backend on LangGraph, add sign-in, replace the motion engine, host everything on AWS | A deployed system at <https://release.d32nf9wwqqt016.amplifyapp.com>: sign-in, chat, knowledge search, motion pipeline |
| Sprint 4 | 18/09 – 09/10/2026 (weeks 1–3) | See 4.1 | Voice live, mobile chat, message feedback, measured agent quality (probe V7), security audit result |
| Sprint 5 | `[TEAM: dates]` | Release the agent-context work, act on the security audit, make motion available without a manual GPU start, test on real phones | Items marked S5 in the backlog below |

### 4.4 Sprint backlog

Priority uses MoSCoW. Estimates are story points on a 1/2/3/5/8 scale: 1 is about half a day, 2 about one day, 3 about two to three days, 5 about a week, 8 more than a week. All values are *(proposed)* from the real time span of the work and must be confirmed by the owner. "Sprint / week" shows when the item was worked on: Carry-over, S4-W1 (18–25/09), S4-W2 (26/09–02/10), S4-W3 (03–09/10), or S5 (planned next).

| ID | Epic | Backlog item | Acceptance criteria | Priority | Estimate (SP) | Owner | Sprint / week | Depends on |
|---|---|---|---|---|---|---|---|---|
| AUTH-01 | Auth | As a user, I can create an account and sign in with email so my chats are private | Sign-up, sign-in and sign-out work on the hosted site; a protected page sends a signed-out visitor to the login page | Must | 5 | Tri | Carry-over | FE-01 |
| AUTH-02 | Auth | As a user, I can sign in with Google and link it to my account | Google sign-in works; linking does not create a second account; password can be changed | Must | 5 | Tri | Carry-over | AUTH-01 |
| AUTH-03 | Auth | As the system, I accept an API call only with a valid login token | The backend checks the token signature against the Cognito public keys; a missing or invalid token gets 401 | Must | 3 | Nguyen | Carry-over | AUTH-01 |
| AUTH-04 | Auth | As a user who picked the wrong Google account, I can get back to my own account | Logging out ends the Cognito session; the Google account chooser always appears after logout | Must | 5 | Nguyen | Carry-over | AUTH-02 |
| AUTH-05 | Auth | As a user, only I can read or change my data | No setting can turn sign-in off; the user ID comes from the token only; row-level security on the six user tables; a query for another user returns nothing | Must | 8 | Tri | Carry-over | AUTH-03, PLAT-01 |
| AUTH-06 | Auth | As the operator, the API has one protected front door | API Gateway checks the Cognito token and limits request rate; the direct function address is closed | Must | 3 | Tri | Carry-over | AUTH-03 |
| AUTH-07 | Auth | As the team, we know how the system stands against OWASP ASVS 5.0 | All 345 requirements have a verdict with evidence; a remediation plan in waves exists | Should | 5 | Tri | Carry-over | AUTH-05, AUTH-06 |
| AUTH-08 | Auth | Security remediation, waves 1–6 | Each wave is deployed and tested; a checklist row changes only when its test evidence exists | Should | 8 | Tri | S5 | AUTH-07 |
| AUTH-09 | Auth | Picking the wrong Google account must not create a stray user | No new Cognito user and no mapping row are created; checked on the deployed user pool | Should | 3 | `[TEAM]` | S5 | AUTH-04 |
| AUTH-10 | Auth | Data isolation is proven on the live database | An automated test with two users on the hosted database passes | Must | 2 | Tri | S4-W2 | AUTH-05 |
| FE-01 | Frontend | As a user, I use a modern web app instead of the test page | React 19 + Vite app builds and is served by Amplify from the `release` branch | Must | 5 | Tri | Carry-over | — |
| FE-02 | Frontend | As a user, I see a 3D avatar on a stage | Avatar model, ground and camera render in the browser | Must | 5 | Tony Lee | Carry-over | FE-01 |
| FE-03 | Frontend | As a user, I see the answer appear as it is written | The chat panel streams the real backend answer; a web-search toggle is available | Must | 5 | Nguyen | Carry-over | LG-03, AUTH-03 |
| FE-04 | Frontend | As a user, a page refresh does not lose my conversation | The conversation is restored after refresh; a "new conversation" button exists | Must | 3 | Nguyen | Carry-over | FE-03 |
| FE-05 | Frontend | As a user, the avatar moves naturally | Facial animation, head follow, retargeted animations, gestures and smooth blending work without a T-pose flash | Should | 8 | Tri | Carry-over | FE-02 |
| FE-06 | Frontend | As a user, I choose my character and my settings follow me | Character list, avatar background picker; preferences load on any device after sign-in | Should | 5 | Tri | Carry-over | AUTH-05 |
| FE-07 | Frontend | As a user, I choose the website language separately from the reply language | English and Vietnamese interface strings; the choice does not change the assistant's reply language | Should | 3 | Tri | Carry-over | FE-01 |
| FE-08 | Frontend | As the team, the frontend builds cleanly | Lint errors cleared; Node and npm versions pinned | Should | 3 | Tri | Carry-over, S4-W2 | FE-01 |
| FE-09 | Frontend | As a phone user, I can chat comfortably | Chat dock on phone width, resizable by dragging; motion replay chips reachable without opening the chat | Must | 5 | Tri | S4-W1, S4-W2 | FE-03 |
| FE-10 | Frontend | As a user, the avatar stage looks finished | Clothing physics, shadows, floor and background, camera track during animations | Should | 5 | Tony Lee | S4-W1, S4-W2 | FE-05 |
| FE-11 | Frontend | As a user, I can rate an answer | Thumbs up/down is saved per message; thumbs down asks for a reason | Should | 3 | Tri | S4-W2 | AUTH-05 |
| FE-12 | Frontend | As a user, I can hear the answer and speak my question | Speaker button plays the reply without gaps; one synthesis at a time; voice input types into the chat box | Should | 5 | Tri | S4-W1, S4-W2 | PLAT-03 |
| FE-13 | Frontend | Mobile layout changes and camera panning limits | `[owner]` | Should | 3 | Tony Lee | S4-W3 | FE-09 |
| FE-14 | Frontend | As a phone user, I install the app | Android and iOS builds run on real devices, including sign-in | Could | 5 | `[TEAM]` | S5 | AUTH-02 |
| FE-15 | Frontend | As a user, I can send an image in the chat | The image is uploaded and reaches the agent | Could | 3 | `[TEAM]` | S5 | FE-03 |
| KIM-01 | Kimodo | Choose the motion engine | Decision recorded with its reason: Kimodo replaces DART because it supports joint-angle limits | Must | 2 | Team | Carry-over | — |
| KIM-02 | Kimodo | As the operator, Kimodo runs on a GPU server in AWS | Container builds in CI; the service starts on a g5.xlarge; model weights are kept outside the image | Must | 8 | Tri | Carry-over | KIM-01 |
| KIM-03 | Kimodo | Convert Kimodo output for the avatar | NPZ output is converted to BVH that the browser can play | Must | 3 | Tri | Carry-over | KIM-02 |
| KIM-04 | Kimodo | As the agent, I request a motion without waiting on the GPU | Job queue with statuses; worker heartbeat; result stored and served by a signed link that expires | Must | 8 | Tri | Carry-over | KIM-02, AUTH-06 |
| KIM-05 | Kimodo | Prove the motion path end to end | One request goes from queue to finished file on the real GPU; unsigned and expired links are refused | Must | 3 | Tri | Carry-over | KIM-04 |
| KIM-06 | Kimodo | As a user, I watch the avatar perform the exercise | The generated motion plays on the avatar; motions of the conversation remain after refresh | Must | 5 | Tri | Carry-over | KIM-04, FE-05 |
| KIM-07 | Kimodo | Motion starts from what the answer needs | Motion runs when the planner sets the motion tag, not from a separate flag | Should | 3 | Tri | S4-W2 | LG-08 |
| KIM-08 | Kimodo | Motion is available without a manual GPU start | GPU starts on demand or on a schedule; first motion is ready faster than the current 5-minute cold start | Should | 5 | `[TEAM]` | S5 | KIM-05 |
| LG-01 | LangGraph | Record the architecture decisions | ADR-001 to ADR-005 and the plan are written and agreed | Must | 5 | Nguyen | Carry-over | — |
| LG-02 | LangGraph | As the system, the agent runs as a LangGraph service | Graph with tool servers and a `/chat` API; speech runs in the background | Must | 8 | Nguyen | Carry-over | LG-01 |
| LG-03 | LangGraph | As a user, I get a streamed, persona-styled answer with web search | The chat endpoint streams events; web search can be toggled; persona applied when the answer is written | Must | 5 | Nguyen | Carry-over | LG-02 |
| LG-04 | LangGraph | Rebuild memory, planner and retriever | Planner decides outputs and routing; memory and retrieval work; the test suite is green | Must | 8 | Nguyen | Carry-over | LG-02 |
| LG-05 | LangGraph | Remove the old orchestrator | The old package is deleted and CI tests the LangGraph service | Should | 3 | Nguyen | Carry-over | LG-04 |
| LG-06 | LangGraph | As the operator, the agent is hosted on AWS | Agent runs on Lambda from a container image; deployed by CI/CD; the frontend calls it | Must | 8 | Tri | Carry-over | PLAT-01, AUTH-06 |
| LG-07 | LangGraph | YouTube transcript ingestion | `[owner]` | Could | 3 | Nguyen | Carry-over | LG-04 |
| LG-08 | LangGraph | As a user, I get answers that use the right tool and safe doses | Tool chosen from tool descriptions; a dose appears only if the evidence states it; personas read from files; measured by the probe | Must | 8 | Tri | S4-W2, S4-W3 | LG-04 |
| LG-09 | LangGraph | Grading evaluation scripts and NHS knowledge ingestion | `[owner]` | Should | 5 | Nguyen | S4-W3 | LG-04 |
| LG-10 | LangGraph | Release the agent-context work and redesign the grader | Probe passes its gates (grader retries at or below 3); work merged to `release` | Must | 5 | `[TEAM]` | S5 | LG-08 |
| PLAT-01 | Platform | As the system, data lives in one hosted database | PostgreSQL with pgvector on Neon in the same region as the agent; no data lost in the move | Must | 5 | Nguyen | Carry-over | — |
| PLAT-02 | Platform | Each character has its own model and persona | Models served from cloud storage; persona per character | Should | 5 | Tri | Carry-over | PLAT-01 |
| PLAT-03 | Platform | As a user, I hear the answer in Vietnamese | Speech is streamed to the browser; live on the production site | Must | 8 | Tri | Carry-over, S4-W1 | LG-06 |
| PLAT-04 | Platform | Fix the agent running out of memory | Knowledge-search questions complete in production | Must | 2 | Tri | Carry-over | LG-06 |
| PLAT-05 | Platform | Billing demo page and API | `[owner]` | Could | 3 | Tony Lee | Carry-over | AUTH-01 |
| PLAT-06 | Platform | Recover a stuck hosted deployment and configure sign-in per environment | `[owner]` | Should | 3 | Nguyen | Carry-over | FE-01 |
| PLAT-07 | Platform | Voice cloning and language detection | `[owner]` | Could | 3 | Nguyen | Carry-over | PLAT-03 |

### 4.5 Story points by epic

Computed from the proposed values above; it must be recomputed after owners confirm them.

| Epic | Total | Done and released | Done on feature branch, not released | In progress (week 3) | Planned for Sprint 5 |
|---|---|---|---|---|---|
| Auth | 47 | 29 | 7 | 0 | 11 |
| Frontend | 66 | 55 | 0 | 3 | 8 |
| Kimodo | 37 | 29 | 3 | 0 | 5 |
| LangGraph | 58 | 40 | 8 | 5 | 5 |
| Platform | 29 | 29 | 0 | 0 | 0 |
| **All** | **237** | **182** | **18** | **8** | **29** |

### 4.6 Artifacts

| Artifact | Type | Owner | Attached as |
|---|---|---|---|
| Deployed web application | Running software | Team | Link: <https://release.d32nf9wwqqt016.amplifyapp.com> |
| Source repository | Code | Team | Link: `github.com/Beginner-Mon/ProjectECA` |
| AWS topology diagram | Architecture diagram | Tri | Export of `docs/architecture/aws-topology.drawio` (Figure 12) |
| Sign-in sequence diagram | Architecture diagram | Tri | Export of `docs/architecture/cognito-auth-sequence.drawio` (Figure 6) |
| LangGraph flow and persona reference | Design document | `[TEAM]` | `docs/architecture/langgraph-flow-persona.md` (Figure 13) |
| OWASP ASVS 5.0 checklist and remediation plan | Security audit | Tri | `docs/security/asvs-5.0-checklist.csv`, `docs/plans/asvs-remediation-plan.md` |
| Context probe reports V0–V7 | Test report | Tri | `docs/tracking/context-probe-V*.md` |
| Engineering worklogs (69 dated files) | Progress record | Team | `docs/worklogs/` (Figure 3) |
| Automated test suites | Tests | Team | Run summaries (Figures 15, 16) |

## 5. Risk management plan

### 5.1 What changed since Semester 1

`[One paragraph after reading the Semester 1 register: which old risks are closed, which changed rating, and why.]`

Every risk below comes from something that actually happened in this period, so each has a dated trigger. Scales: likelihood and impact from 1 (low) to 5 (high); rating = likelihood × impact; 15 or more is high, 8–14 medium, 7 or less low.

### 5.2 Risk register

| ID | Category | Risk | Trigger / evidence this period | L | I | Rating | Mitigation (to prevent) | Contingency (if it happens) | Owner | Status | Change since Sem 1 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `[R1…Rn]` | — | `[risks carried over from the Semester 1 register, with their old IDs]` | | | | | | | | | Unchanged / Updated / Closed |
| R-P1 | Process | Finished work exists on one laptop only and is lost or cannot be reviewed | 12/09: a bulk `git restore` wiped a full day of uncommitted work. Until 30/09 the feature branch on GitHub was 18 commits behind. `owasp-check` is still not on GitHub | 4 | 4 | 16 | Commit the same day; push the feature branch before `release` (five-step ship flow); automated helpers are forbidden to run restore, reset or stash | Rebuild from tool transcripts (on 12/09 this recovered 32 files) | Tri | Open | New |
| R-P2 | Process | The task board does not show the real work, so progress is not visible to the supervisor and client | Jira has fewer tasks than were done; the real record is 69 dated worklogs in the repository | 5 | 3 | 15 | Every Sprint 5 backlog item gets a Jira key; board updated at each team meeting | Rebuild the status from the backlog table in section 4.4 | `[Scrum master]` | Open | New |
| R-P3 | Process | Project documents describe a system that no longer exists | The status file said "no AWS credentials" while production was live, and said row-level security was inactive when it was active | 4 | 3 | 12 | One dated worklog is the source of truth; "is it deployed?" is answered only from `origin/release` | Stop and re-check against the live system before acting on a document | `[TEAM]` | Open | New |
| R-P4 | Process | A dependency lockfile change breaks the hosted build | Hosted deployment #21 failed; the same lockfile problem came back three times | 3 | 4 | 12 | Node and npm versions pinned; install with `npm ci` only | Restore the last lockfile that built | Tri | Mitigated | New |
| R-P5 | Process | The deployment pipeline is broken and nobody notices | 29/08 – 05/09: nothing reached production for a week after the repository was renamed | 2 | 5 | 10 | Check the hosted build after every push to `release` and write the build number in the worklog | Reconnect the repository in the hosting console | Tri | Mitigated | New |
| R-P6 | Process | A design choice is accepted without measuring, then rebuilt | GPU cold start assumed 38 seconds, measured about 5 minutes; load balancer built for Kimodo, then removed | 3 | 3 | 9 | Each plan includes a measuring step before the build (as done for voice and for agent context) | Re-plan the item with the measured value | Team | Mitigated | New |
| R-P7 | Process | Work is pushed to `release` by accident and deployed | 28/09: a local branch was created from `origin/release`, so every Sync/Push went straight to `release` | 2 | 4 | 8 | Never work on `release`; cut a feature branch first; upstream removed from that branch | Revert on `release` and redeploy | Team | Mitigated | New |
| R-H1 | People | Knowledge of AWS and deployment sits with one member | Deployment and infrastructure commits come from one account; at least one other member's machine has no AWS access (`docs/tracking/status.md`) | 4 | 5 | 20 | Runbooks in `docs/ops/`; a read-only AWS policy for a second member; a second member performs the next release | The member with access deploys while pairing by screen share | Tri | Open | New |
| R-H2 | People | It is unclear who did a piece of work, which affects individual marks | One commit (`c89f9295`) contains work by two members | 3 | 3 | 9 | One author per commit; co-author line when pairing | Agree the split in the team meeting and record it | Team | Open | New |
| R-H3 | People | Members' machines behave differently, so tests fail for the wrong reason | The wrong Python interpreter gave 8 false failing tests; one laptop cannot run Docker or Redis | 3 | 3 | 9 | The backend prints its interpreter and missing packages at start; a no-Redis mode exists | Run the suite on the member's machine that matches production | Team | Mitigated | New |
| R-T1 | Project | The agent runs out of memory in production | 14/09: 5 of 5 knowledge or motion questions failed at 1024 MB | 3 | 5 | 15 | Memory raised to 2048 MB; watch peak memory after each release | Raise memory again; move the embedding model out of the function | Tri | Mitigated | New |
| R-T2 | Project | The voice service is near its memory limit and slower than real time | Measured peak 2932 of 3008 MB; speech about 1.5 times slower than real time | 3 | 3 | 9 | Reply capped at 600 characters for speech; audio buffered before play | Turn voice off for the session; text answer still works | Tri | Open | New |
| R-T3 | Project | Motion is not available when a user or the client asks for it | The GPU is switched on by hand to save cost; cold start is about 5 minutes; on 28/08 AWS had no g5.xlarge capacity in one zone | 4 | 4 | 16 | GPU group spans two zones; the agent checks the worker heartbeat and answers without motion if it is down; start the GPU before every demo | Show a recorded motion from an earlier session | Tri | Open (KIM-08) | New |
| R-T4 | Project | The knowledge base is emptied by a failed re-load | 05/08: the load script crashed after its reset step and left the table empty | 2 | 5 | 10 | The script now embeds first, then deletes and inserts in one transaction, and refuses to run while the backend is up | Re-load from the source documents (2918 rows) | `[TEAM]` | Closed | New |
| R-T5 | Project | One user sees another user's health-related conversation | In a test on the pooled database connection, 98 of 200 reads saw another user's ID with a plain `SET`; the app also shipped a switch that could turn sign-in off | 2 | 5 | 10 | Row-level security on six tables that fails with an error if no user is set; `set_config(…, true)` leaked 0 of 200; the switch was deleted; live two-user test (AUTH-10) | Take the API offline, rotate database credentials, notify affected users | Tri | Mitigated | New |
| R-T6 | Project | The assistant gives an exercise dose or advice that the evidence does not support | Probe V6 failed three dose checks. V7: 0 of 10 doses outside the evidence, but 7 grader retries against a limit of 3 | 3 | 5 | 15 | A dose is written only if the retrieved evidence states it; fixed safety wording; the probe is run before each release | Roll back to the previous agent version on `release` | Tri | Open (LG-10) | New |
| R-T7 | Project | Known security gaps stay open | The ASVS 5.0 audit baseline is 93 FAIL and 47 PARTIAL out of 345; the first fixes are on a branch that is not merged | 4 | 4 | 16 | Remediation in six waves, each with tests before the checklist changes | Restrict the site to the team and client until wave 1 is deployed | Tri | Open (AUTH-08) | New |
| R-T8 | Project | Voice input sends the user's speech to a third party without clear notice | Dictation uses the browser's speech service (Google, Microsoft or Apple, depending on the browser) | 3 | 4 | 12 | Show a notice before first use and make dictation opt-in | Disable dictation | `[TEAM]` | Open | New |
| R-T9 | Project | The mobile app cannot be delivered | No Android or iOS build has run on a device; mobile sign-in needs a domain the team does not have | 4 | 3 | 12 | Use the responsive website on phones (FE-09) as the mobile deliverable for now | Agree with the client that the mobile app is out of scope | `[TEAM]` | Open (FE-14) | New |
| R-T10 | Project | The language-model provider is slow or down, so answers take too long | Latency study in `docs/fixes/latency-optimization-1234.md` | 3 | 3 | 9 | Timeout, retry and a fallback provider; answer length bounded; prompt caching checked | Tell the user the service is busy instead of waiting | `[TEAM]` | Mitigated | New |
| R-T11 | Project | AWS cost grows beyond a student budget | A g5.xlarge costs about $1 per hour while idle; a load balancer bills every hour | 3 | 3 | 9 | GPU kept at zero and started by hand; queue instead of a load balancer (a 16-minute GPU test cost about $0.27) | Shut the GPU group down and serve cached motions only | Tri | Mitigated | New |

## 6. Sprint progress

One member commits under two git names, "Tony Lee" and "Le Hac Du". Both are the same person.

### 6.1 Where each epic started and where it is now

| Epic | Start of the period (end of April) | Now (03/10/2026) | Still open |
|---|---|---|---|
| Auth | No sign-in; it existed only as a design | Cognito sign-in with Google, token check on every API call, row-level security on six tables, one protected API entry point; live | Stray user when the wrong Google account is picked; mobile sign-in; security remediation waves |
| Frontend | A plain test page | React 19 app on Amplify with a 3D avatar, streamed chat, mobile chat dock, two languages, preferences, message feedback; 425 automated tests | Image upload; app on real phones |
| Kimodo | An older motion engine (DART) on a local port | Kimodo on a GPU server with a job queue, storage and signed links; the avatar plays the generated motion | GPU is started by hand; 5-minute cold start |
| LangGraph agent | An older orchestrator with Firebase, ChromaDB and Celery | An eight-step LangGraph agent on AWS Lambda with a hosted database; 905 automated tests | Agent-context work (35 commits) not yet on `release`; grader redesign |
| Platform & voice | Local database; no hosted services | Hosted database in the same region as the agent; Vietnamese voice live since 23/09 | Voice near its memory limit |

### 6.2 Progress tracking

Status values: **Released** (on `origin/release` and live), **Done, not released** (finished on a branch other than `release`), **In progress**, **Not started**, **Blocked**. Commit hashes can be opened on GitHub. Worklog names are files in `docs/worklogs/`.

| ID | Status | Evidence | Worked on by | Variance note |
|---|---|---|---|---|
| AUTH-01 | Released | `08e8e4f3`, `dafb4bd0`; Figure 5 | Tri | — |
| AUTH-02 | Released | `8cb1bcc6`, `49bcdc3c`, `e2af651f` | Tri | — |
| AUTH-03 | Released | `b857c36e`; `02-07-2026.md` | Nguyen | — |
| AUTH-04 | Released | `96c671ae`, `6271376b`, `b332aafe`; `docs/auth-google-incident.md` | Nguyen | One case left open, moved to AUTH-09 |
| AUTH-05 | Released | `634c47ee`, `7f078c71`; `18-08-2026.md`; Figure 7 | Tri | A test on the pooled connection changed the design (see R-T5) |
| AUTH-06 | Released | `d4781fec`, `21f8f1dc`; Figure 12 | Tri | — |
| AUTH-07 | Done, not released | Branch `owasp-check` (`0dfadee7`, `1f3b754b`) — **not on GitHub yet**; `docs/plans/asvs-remediation-plan.md`; Figure 18 | Tri | Audit finished; first fixes were partly backed out on 18/09 (`8f79fff2`) |
| AUTH-08 | In progress | `docs/plans/asvs-remediation-plan.md` | Tri | Planned for Sprint 5 |
| AUTH-09 | Not started | `09-09-2026.md` | — | Planned for Sprint 5 |
| AUTH-10 | Done, not released | `3fd6ea27`; `01-10-2026.md` | Tri | — |
| FE-01 | Released | `50d87588`, `0dbc15af`, `031a901b`; Figure 17 | Tri | — |
| FE-02 | Released | `14bc5fb8`, `5bc6542b`, `27bdda83`, `5fa1cd6f`, `8abd95fa` | Tony Lee, Tri | — |
| FE-03 | Released | `b857c36e`, `6965582d`, `35985d5d`; Figure 8 | Nguyen | — |
| FE-04 | Released | `9fcae539`, `eea2ceea`; `05-08-2026.md` | Nguyen, Tri | — |
| FE-05 | Released | `05687f53`, `fbe74d4b`, `fc3dd328`, `d948019f`, `1e2448d8`, `6f4c17f2`, `eb484f9d` | Tri, Nguyen | — |
| FE-06 | Released | `a9c4ce3a`, `fa8cd83e`, `484bb327`, `3a959b3c`; Figure 10 | Tri | — |
| FE-07 | Released | `32bb9c03`; Figure 10 | Tri | — |
| FE-08 | Released | `ee71f4a6`, `2b3ea54a`; `29-09-2026.md` (415 tests pass) | Tri, Tony Lee | Lockfile problem returned three times (see R-P4) |
| FE-09 | Released | `cd7816bf`, `ef2faff7`; weekly worklog week 2; Figure 9 | Tri | Started in week 1, finished in week 2 after team feedback |
| FE-10 | Released | `bf9bb0d2`, `23e5e59a`, `59335e49`, `c89f9295`, `f2b21a7f`, `e4b17aad`, `ce1948b2` | Tony Lee | Some commits reached `release` through a mis-set upstream (see R-P7) |
| FE-11 | Released | `3bb05e8c`, `50593a0b`, `53b3ff73`, `161badcd`, `c7524390`; Figure 8 | Tri | — |
| FE-12 | Released | `885ec019`, `0fb60503`, `594437ab`; weekly worklogs weeks 1–2 | Tri | Dictation attribution to be agreed (open item 10) |
| FE-13 | In progress | `9798a4f6`, `cda73fef` (on GitHub, not on `release`) | Tony Lee | Week 3 |
| FE-14 | Blocked | `74c7e303` (project scaffold only); `docs/mobile-app-links.md` | Tri | No device build tools and no domain; planned for Sprint 5 |
| FE-15 | Not started | — | — | Planned for Sprint 5 |
| KIM-01 | Released | `20-05-2026.md` | Team | — |
| KIM-02 | Released | `ec62f1b0`, `36f94493`, `659051de`; `29-06-2026.md` | Tri | CI image split in two after a disk overflow |
| KIM-03 | Released | `31fd49eb`, `c7bf7e4d` | Tri | — |
| KIM-04 | Released | `07a24ca1`, `189fc49f`, `bf00cd2e`, `4d28a612`; Figure 12 | Tri | Replaced the planned load balancer (see 7.4) |
| KIM-05 | Released | `c4f3faf4`; `28-08-2026.md` (7 s request to done; 63,592-byte file) | Tri | Five failed deployments before it worked; cold start 5 minutes, not 38 seconds |
| KIM-06 | Released | `1cdde23c`, `5eb08461`, `352e1cf8`; Figure 11 | Tri | — |
| KIM-07 | Done, not released | `8b3b26c0`; `02-10-2026.md` | Tri | A first design (a "show movement" tool) reached only 50% and was dropped |
| KIM-08 | Not started | — | — | Team decision pending; planned for Sprint 5 |
| LG-01 | Released | `dae84863`; `19-05-2026.md`, `20-05-2026.md` | Nguyen | — |
| LG-02 | Released | `492d8a4e`, `22934306`, `66137e80`, `858e8a8b` | Nguyen | — |
| LG-03 | Released | `e97d6026`; `26-05-2026.md` | Nguyen | — |
| LG-04 | Released | `47c67d0f`, `e720a291`; `11-06-2026.md` (187 of 187 tests) | Nguyen | — |
| LG-05 | Released | `af88ed8e`, `e00d60c7`; `10-08-2026.md` | Nguyen | — |
| LG-06 | Released | `069bb501`, `59d6bc6e`; `docs/tracking/agent-deploy-handoff.md` (481 tests) | Tri | — |
| LG-07 | Released | `25831133` | Nguyen | — |
| LG-08 | Done, not released | `39437717`, `2b54aec8`, `654e8064`, `1a0f62d3`; `docs/tracking/context-probe-V7.md`; Figure 14 | Tri | V7 missed four gates; grader redesign moved to LG-10 |
| LG-09 | In progress | `7867302b` (on GitHub, not on `release`) | Nguyen | Week 3 |
| LG-10 | Not started | `0b634ca0` (backlog note) | — | Planned for Sprint 5 |
| PLAT-01 | Released | `d7851fb7`; `docs/ops/neon-migration-us-east-1.md` | Nguyen | Moved a second time on 17/08 to sit in the same region as the agent |
| PLAT-02 | Released | `68963211` | Tri | — |
| PLAT-03 | Released | `885ec019`, `818487d1`, `132825e1`, `36042826`, `0fb60503`; `23-09-2026.md` | Tri | Choppy audio on the live site only; fixed by using the CDN |
| PLAT-04 | Released | `dc6ce970`; `14-09-2026.md` | Tri | — |
| PLAT-05 | Released | `ea574803`, `3036b67c` | Tony Lee | — |
| PLAT-06 | Released | `5071a530`, `d6893571`, `4c539c13` | Nguyen | — |
| PLAT-07 | Released | `73219019` | Nguyen | — |

### 6.3 Milestone timeline

| Date | Milestone | Epic | Who (git author) | Evidence |
|---|---|---|---|---|
| 19–20/05 | Architecture decisions and plan written | LangGraph | Nguyen | `dae84863`; `19-05-2026.md` |
| 23–24/05 | LangGraph v2.4 service with tool servers and `/chat` | LangGraph | Nguyen | `492d8a4e`, `66137e80` |
| 02–03/06 | New React app with Amplify sign-in | Frontend, Auth | Tri | `50d87588`, `08e8e4f3` |
| 05–09/06 | 3D viewer, ground and camera | Frontend | Tony Lee | `14bc5fb8`, `5fa1cd6f` |
| 08–11/06 | Google sign-in and account linking | Auth | Tri | `8cb1bcc6`, `e2af651f` |
| 11/06 | Backend test suite rebuilt: 33 failures to 187 of 187 passing | LangGraph | see worklog | `11-06-2026.md` |
| 02/07 | Backend checks login tokens; chat panel shows real answers | Auth, Frontend | Nguyen | `b857c36e`, `6965582d` |
| 02–03/07 | Kimodo GPU service defined on AWS | Kimodo | Tri | `36f94493`, `659051de` |
| 23–31/07 | Avatar animation: facial, T-pose fix, retargeting, ground clamp | Frontend | Nguyen, Tri | `05687f53`, `d948019f`, `fc3dd328` |
| 05/08 | Database moved to Neon; conversation survives a refresh | Platform, Frontend | Nguyen | `d7851fb7`, `9fcae539` |
| 05–09/08 | Google wrong-account incident fixed | Auth | Nguyen | `96c671ae`, `b332aafe` |
| 10/08 | Old orchestrator deleted (71 files) | LangGraph | Nguyen | `e00d60c7` |
| 17/08 | Database moved to the agent's region; warm call 436 ms to 5 ms | Platform | Nguyen | `docs/ops/neon-migration-us-east-1.md` |
| 18–20/08 | Sign-in bypass removed; row-level security; protected API entry | Auth | Tri | `634c47ee`, `d4781fec` |
| 21/08 | Agent hosted on AWS Lambda | LangGraph | Tri | `069bb501` |
| 27–29/08 | Motion queue, GPU test end to end, avatar plays the motion | Kimodo | Tri | `07a24ca1`, `c4f3faf4`, `1cdde23c` |
| 29/08–05/09 | Hosted build broken for a week, then repaired | Process | — | `05-09-2026.md` |
| 01–05/09 | Preferences, avatar background, website language | Frontend | Tri | `484bb327`, `fa8cd83e`, `32bb9c03` |
| 12/09 | A day of uncommitted work lost and rebuilt | Process | — | see 8.2 |
| 14–15/09 | Agent out-of-memory failure fixed | Platform | Tri | `dc6ce970`; `14-09-2026.md` |
| 15–18/09 | Security audit against OWASP ASVS 5.0 | Auth | Tri | branch `owasp-check` |
| 23/09 | Avatar speaks on the live site | Platform | Tri | `132825e1`, `0fb60503` |
| 19–28/09 | Avatar stage: physics, shadow, background, camera | Frontend | Tony Lee | `23e5e59a`, `c89f9295`, `ce1948b2` |
| 25–26/09 | Mobile chat dock | Frontend | Tri | `cd7816bf`, `ef2faff7` |
| 29–30/09 | Thumbs up/down on each answer | Frontend | Tri | `161badcd`, `53b3ff73` |
| 01–03/10 | Agent-context rounds 1–3 measured with probes V0–V7 | LangGraph | Tri | `39437717` … `1a0f62d3` |

### 6.4 Metrics

Commits per month (GitHub, merges excluded, duplicates removed; Figures 1 and 2):

| Month | May | June | July | August | September | October (to 03/10) |
|---|---|---|---|---|---|---|
| Commits | 12 | 50 | 48 | 181 | 161 | 37 |

Automated tests at dated points (Figures 15 and 16):

| Date | Backend tests passing | Frontend tests passing | Source |
|---|---|---|---|
| 11/06 | 187 | — (no test runner yet) | `11-06-2026.md` |
| 08/08 | — | 44 | `docs/tracking/status.md` |
| 19/08 | 331 | — | `docs/tracking/status.md` |
| 21/08 | 481 | — | `docs/tracking/agent-deploy-handoff.md` |
| 22/09 | — | 295 | `22-09-2026.md` |
| 29/09 | — | 415 | `29-09-2026.md` |
| 01/10 | 766 (2 failing) | — | `01-10-2026.md` |
| 02/10 | 868 (1 failing, 16 skipped) | 425 | `02-10-2026.md` |
| 03/10 | 905 (1 failing) | 425 | `02-10-2026-round3.md`, `03-10-2026.md` |

The one failing backend test was already failing before this sprint's changes.

Agent quality, probe V7 (51 questions; `02-10-2026-round3.md`, Figure 14):

| Check | Target | Result |
|---|---|---|
| Doses that are not in the evidence | 0 | 0 of 10 — pass |
| Motion runs when the motion tag is set | all | 17 of 17 — pass |
| Answers that cite their source | 6 of 6 | 5 of 6 — fail |
| Grader retries | 3 or fewer | 7 — fail |
| Safety warning check, Vietnamese | pass | pass with warning — fail |
| Persona voice (Anne) | no worse than V6 (19 of 25) | 19 of 26 — fail by one answer |

Across the earlier rounds, small talk that still pushed an exercise fell from 7 of 8 answers (V0) to 1 of 8 (V4-bis), against a target of 10% or less (`01-10-2026.md`).

### 6.5 How the team worked together

Rows show backlog items where the commits of more than one member build on each other.

| Backlog ID | First contribution | Built on by |
|---|---|---|
| AUTH-01 to AUTH-04 | Tri: sign-in and Google on the frontend (`08e8e4f3`, `8cb1bcc6`) | Nguyen: token check in the backend (`b857c36e`) and Google account flow fixes (`96c671ae`, `b332aafe`) |
| FE-02, FE-05 | Tony Lee: 3D viewer, ground and camera (`14bc5fb8`, `5fa1cd6f`) | Tri: camera target and animation system (`8abd95fa`, `1e2448d8`); Nguyen: facial animation and ground clamp (`05687f53`, `fc3dd328`) |
| FE-03, FE-09 | Nguyen: chat panel connected to the streaming backend (`6965582d`, `35985d5d`) | Tri: mobile chat dock on top of it (`cd7816bf`, `ef2faff7`) |
| KIM-06, FE-10 | Tri: generated motion plays on the avatar (`1cdde23c`) | Tony Lee: animation lock, clothing physics and camera track during animations (`bf9bb0d2`, `23e5e59a`, `f2b21a7f`) |
| PLAT-01, LG-06 | Nguyen: database moved to Neon (`d7851fb7`) | Tri: agent hosted in the same region (`069bb501`) |
| FE-08 | Tri: lint backlog fix (`ee71f4a6`) | Tony Lee: last lint errors cleared (`2b3ea54a`) |

### 6.6 Task board

`[Figure 4: Jira board.]` The Jira board does not contain all of the work in the table above. During this period the team recorded work in dated worklogs inside the repository (Figure 3), and these, together with the commit history, are the complete record. Section 8.2 explains why this happened and section 9 sets the action to fix it.

## 7. Sprint review (critical review of the product)

### 7.1 Demonstration session

| Date | Attendees | Format | Facilitated by |
|---|---|---|---|
| `[from client notes]` | `[from client notes]` | `[in person / online]` | `[from client notes]` |

`[Two or three sentences on how the session was run: agenda sent beforehand, who presented which part, how questions were handled, how feedback was recorded.]`

### 7.2 What was demonstrated and what the client said

| # | Feature shown | Shown by | Client feedback | Deliverable? (Y/N) | Follow-up (backlog ID) |
|---|---|---|---|---|---|
| 1 | `[from client notes]` | | | | |
| 2 | | | | | |
| 3 | | | | | |
| 4 | | | | | |

### 7.3 Plan against actual

The plan below is the priority list the product owner set on 30/07 (`docs/tracking/status.md`). `[Add the Sprint 3 plan rows when the Sprint 3 report is available.]`

| Backlog ID | Planned | Achieved | Variance | Reason |
|---|---|---|---|---|
| PLAT-01 | Move the database to Neon | Done 05/08; moved again 17/08 | A second move was needed | The first region was far from the agent; the same region cut a warm call from 436 ms to 5 ms |
| AUTH-04, AUTH-05 | Deploy the sign-in fixes | Done; live in production | Late | No AWS access at the start of August, so the code could not be run for real. `[TEAM: go-live date]` |
| KIM-04 | Deploy Kimodo behind a load balancer | Not done as planned; replaced by a job queue on 27–28/08 | Design changed | A load balancer bills every hour even when the GPU is off, and GPU work runs one job at a time |
| FE-05 | Refactor the animation state machine | Done 30/07 (17 of 17 tests) | None | — |
| KIM-06 | Motion delivery: generate, convert, serve, notify, play | Done 29/08 | None | — |
| `[TEAM]` | Backend sends the avatar's emotion | `[TEAM to confirm]` | | The reply-emotion setting was switched off at the start of the agent-context work (`39437717`) |
| LG-08 | Agent-context quality gates | Rounds 1–3 done; four V7 gates missed | Not released | Grader retries rose from 3 to 7; redesign moved to Sprint 5 (LG-10) |

### 7.4 Decisions that changed during the period

| Decision | Original | Changed to | Reason |
|---|---|---|---|
| Motion engine | DART | Kimodo | Joint-angle limits are needed for physiotherapy accuracy (`20-05-2026.md`) |
| How the agent reaches Kimodo | A direct tool call, then a load balancer | A job queue with a worker | Cost and GPU jobs running one at a time (`28-08-2026.md`) |
| Approval step before a motion is rendered | Planned in the first design | Removed in the v2.4 re-plan; motion now follows the planner's motion tag and the worker heartbeat | Simpler flow `[TEAM to confirm the reason]` |
| Background jobs | Celery | Plain async tasks | Speech finished fast enough not to need a job system |
| Live updates to the browser | One WebSocket | Server-sent events with a normal POST | Works through the CDN and reconnects by itself |
| Agent steps | Manager, reasoning, validator, dispatch, conversation | Memory, planner, retriever, tools, kimodo, synthesizer, grader, error handler | A simpler graph; persona styling moved into the step that writes the answer |
| Separating test and production sign-in | A switch that turned sign-in off | Each environment trusts its own user pool; no switch | A switch that disables sign-in should not exist in production code (`18-08-2026.md`) |
| Database hosting | Local PostgreSQL | Neon, then moved to the agent's region | Hosted service; latency |

### 7.5 Critical analysis

**What is deliverable.** Everything marked "Released" in section 6.2 is on the `release` branch and running at the public address: 182 of 237 proposed story points. A user can sign in, chat, hear the answer, see a generated exercise on the avatar when the GPU is on, and rate the answer.

**What is not yet deliverable, and why.**

- *Finished but not released.* The agent-context work (LG-08), the tag-driven motion (KIM-07) and the live isolation test (AUTH-10) sit on the feature branch, 35 commits ahead of `release`. They are held back on purpose: probe V7 missed four gates, and releasing an agent that retries its safety check more often would be a step back for users.
- *Audited but not fixed.* The security audit gave an honest baseline of 93 failing and 47 partial requirements out of 345. Finding them is progress, but the fixes are not deployed, and the audit branch is not yet on GitHub.
- *Depends on a manual step.* Motion works only after someone starts the GPU and waits about five minutes. This is acceptable for a planned demo and not acceptable for a real user.
- *Never tested on a device.* The mobile app project exists but no build has run on a phone. The responsive website is the working mobile option today.

**What the plan got wrong.** Several early design choices were built and then removed (section 7.4). Each removal was correct, but each cost time. The common cause was deciding before measuring; section 8.2 analyses this.

`[One paragraph on the client's feedback: which comments confirm the direction, which change priorities, and which backlog items they create.]`

## 8. Retrospect (critical review of the process)

### 8.1 What went well

| Strength | Evidence |
|---|---|
| A dated engineering log for almost every working day | 69 files in `docs/worklogs/` |
| Decisions written down with their reasons | ADR-001 to ADR-010 in the worklogs; plans in `docs/plans/` |
| Tests grew with the product | Backend 187 to 905; frontend 0 to 425 |
| Measuring before releasing | Voice measured live before it was switched on; agent changes measured with eleven probe runs (V0 to V7) |
| Problems were written up, not hidden | `docs/auth-google-incident.md`, `14-09-2026.md`, `05-09-2026.md` |

### 8.2 Process challenges and their causes

| Challenge | Root cause | Impact | How it was addressed | Process change |
|---|---|---|---|---|
| A full day of work was lost on 12/09 | The work stayed uncommitted all day in a working folder shared by several parallel sessions, and one of them ran a bulk restore | One day of voice-streaming work across backend, frontend and CI | 32 files were rebuilt from tool transcripts | Commit the same day on a feature branch; helpers may not run restore, reset or stash |
| The feature branch on GitHub fell 18 commits behind | A shortcut (`git push origin HEAD:release`) deployed the work but updated only the `release` branch | Eleven separate changes had no review page and no backup | The branch was pushed and the shortcut banned | Five-step ship flow: commit, test, push the feature branch, merge into `release`, push `release` |
| Nothing reached production from 29/08 to 05/09 | The repository was renamed, which left the hosting service connected to an old repository ID. The error message pointed to permissions, which hid the real cause | A week of finished work not visible to the client | The repository was reconnected | Check the hosted build after each release push and record the build number |
| The hosted build broke three times on the same lockfile problem | Installing with a different npm version rewrote the lockfile, and merges brought the bad version back | Failed deployments | Node and npm pinned; `npm ci` only | The lockfile is never regenerated with a fresh install |
| Work was pushed to `release` by accident on 28/09 | A local branch was created from `origin/release`, so the editor's Sync button pushed to `release` | Unreviewed changes were deployed | Upstream removed from the branch | Never work on `release`; cut a feature branch as the first step |
| The Jira board does not match the work done | *(to confirm)* The team logged work in the repository next to the code, so Jira became a second place to update and was skipped | The supervisor and client cannot see progress from the board | This report rebuilds the backlog from commits and worklogs | Every Sprint 5 item has a Jira key; board updated at each meeting |
| Status documents misled the team | One long status file was updated by hand with no owner and no check against the live system | Time lost investigating things that were already done; a wrong note said data protection was inactive | A banner now points to the latest dated worklog | "Is it deployed?" is answered only from `origin/release` |
| Features were built and then removed | The first design was fixed before cost and speed were measured (approval step, Celery, load balancer; cold start assumed 38 s, measured 5 minutes) | Rework | Re-plan (v2.4) with smaller steps | Each plan has a measuring step before the build |
| One commit holds two members' work | Changes from two people were in the same working folder when the commit was made | Unclear individual contribution | Listed as an open item for this report | One author per commit; co-author line when pairing |
| Tests failed for the wrong reason on some machines | Different Python interpreters and missing local services between laptops | Eight false failures; time lost | The backend now prints its interpreter and missing packages at start | Run the suite with the documented interpreter before reporting a failure |

### 8.3 Privacy and security review (Australian Privacy Principles)

The assistant handles conversations about injuries and exercise, which is health information and therefore sensitive information under the Privacy Act. No real patient data was used in development. `[TEAM to confirm]`

| Principle | How it applies to ECA | What we did | Gap / next step |
|---|---|---|---|
| APP 1 — open and transparent management | Users should be able to read how their data is handled | — | No privacy notice in the app yet `[TEAM to confirm]`; write one before real users are invited |
| APP 3 — collection, including sensitive information | The app stores email, conversations, summaries, remembered user facts, and message ratings with reasons | Only data needed by a feature is stored | Ask for consent to store health-related conversations |
| APP 5 — notice of collection | Users should know what is collected at the time | The dictation code carries a developer note that speech goes to the browser vendor; there is no notice for users yet | Add a notice at sign-up and before first use of dictation |
| APP 6 — use and disclosure | Conversation text is sent to a language-model provider and, when enabled, to web search; dictation audio goes to the browser's speech service | Web search is a user toggle | Name the third parties in the notice (R-T8) |
| APP 8 — cross-border disclosure | The servers and database are in the United States (AWS and Neon, us-east-1); the language-model provider is overseas | — | State this in the notice |
| APP 11 — security of personal information | Unauthorised access to conversations | Sign-in on every API call; row-level security on six tables that fails closed; no switch to turn sign-in off; request limits; download links that expire; spoken audio cached per user and cleared at sign-out; ASVS 5.0 audit | 93 failing and 47 partial ASVS requirements to fix (AUTH-08) |
| APP 12 and 13 — access and correction; deletion | A user may ask for their data or for its removal | Account-deletion routes exist but are switched off | Switch them on once they also remove the sign-in account `[TEAM to confirm]` |

### 8.4 Team code of conduct and use of AI tools

| Rule from the team code of conduct | Followed? | Note |
|---|---|---|
| `[TEAM]` | | |
| `[TEAM]` | | |
| `[TEAM: statement on the use of AI coding assistants, in line with the unit rules]` | | |

## 9. Lessons learned and actions for the next sprint

| # | Lesson from this sprint | What showed it | Action | Owner | When | How we know it is done |
|---|---|---|---|---|---|---|
| 1 | Work that is not on GitHub is not safe | A day of work lost on 12/09; 18 commits behind; `owasp-check` still local | Push `owasp-check` now; push the feature branch at the end of every working day | Tri; every member | Now; daily | `git rev-list --count origin/<branch>..<branch>` is 0 at the end of the day |
| 2 | A release is not finished until the hosted build is green | A week with nothing reaching production | After each push to `release`, write the build number and result in the worklog | Whoever pushes | Every release | Every release entry in the worklog has a build number |
| 3 | Measure first, then build | Cold start 38 s assumed, 5 minutes measured; load balancer built then removed | Every plan in `docs/plans/` has a "measured" line before the build steps | Plan author | From Sprint 5 | No plan is approved without it |
| 4 | One place for task status | Jira did not match the work | Enter the Sprint 5 backlog in Jira with the IDs from section 4.4; update at each meeting | `[Scrum master]` | Sprint 5, week 1 | Jira "Done" equals the tracking table at the next report |
| 5 | A status document must be dated and checked | The status file said production was blocked while it was live | Replace the body of the status file with a link to the latest worklog; correct the project notes that still list removed features | `[TEAM]` | Sprint 5, week 1 | The status file has no statement older than one sprint |
| 6 | Deployment knowledge must be shared | One member holds AWS access | A second member performs the next release with the runbook | Tri and one other member | Sprint 5 | One release is completed by someone other than Tri |
| 7 | A feature that needs a manual start will fail in a demo | GPU off by default, 5-minute cold start | Start the GPU 10 minutes before any demo; decide on automatic start (KIM-08) | Tri; Team | Next demo; Sprint 5 | Motion works on the first request in the next demo |
| 8 | Safety changes need a number, not an opinion | The probe showed 0 unsupported doses but 7 grader retries | Run the 51-question probe before merging agent work to `release`; redesign the grader (LG-10) | Tri | Sprint 5 | Grader retries at or below 3; unsupported doses stay at 0 |
| 9 | Each commit should have one author | Commit `c89f9295` holds two members' work | Commit own changes separately; add a co-author line when pairing | Every member | From now | No open attribution item in the next report |
| 10 | Privacy has to be designed before real users arrive | Health conversations, overseas servers, dictation through a third party | Write the privacy notice and consent step; make dictation opt-in | `[TEAM]` | Sprint 5 | Notice shown at sign-up; dictation off until accepted |
| 11 | "Mobile" must be tested on a phone | No device build has run | Test the responsive site on two real phones; decide with the client whether the installed app stays in scope | `[TEAM]` | Sprint 5, week 2 | Test notes with screenshots from two devices |
