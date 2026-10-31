---
tags: [plan, avatar, frontend, agent]
status: in-progress
date: 2026-10-31
---

# Avatar emotion follows the query and the action — Plan

> **For the implementing agent.** Do the tasks in order, one slice at a time. Do not
> redesign, merge tasks, or work outside the list. If something the plan did not
> foresee comes up, stop, write it in the worklog, and report to K.

**Goal:** the face reacts to (1) what the user said, from the moment the planner
has read it, and (2) what the avatar is doing — thinking, demonstrating an
exercise, waiting for approval, failing to render. Today it is neutral almost all
the time.

**Why (read from code, 31/10/2026):**

- An emotion is set in only three places: the reply tag (`ChatContext.tsx:785`),
  the greeting clip (`CharacterViewer.tsx:442`) and the mouth-click reaction.
  Idle shows `relaxed` once every 50–60 s (`IdleBehaviorController.ts:19`).
- Every FSM state already declares a face — `facial: { wander, hold }` in
  `lib/AnimationStates.ts:113-262` (exercise → neutral, gesture → happy,
  thinking_* → neutral) — but **`facialOf()` is never called**. The policy is
  dead data. Only `mood: 'thinking'` is wired (`MotionContext.tsx:229`).
- The user's query never reaches the face. The planner runs ~1–2 s before the
  reply, and the `stage {node: planner}` event already reaches the browser
  (`main.py:798`, `ChatContext.tsx:771`), but carries no affect.
- The face picks one emotion per reply and drops to neutral 1.5 s after the voice
  ends (`AvatarController.ts:18`, `:211`).

**Approach:** no new LLM call, no new SSE event type, no new timers beyond the
existing engagement deadline. Reuse the planner's structured output and the
existing `stage` event; make the FSM's declared `facial` policy real.

**Rules that stay:** `angry` never shown; safety content (`red_flag_screen`,
`referral_advice`) → neutral; per-emotion caps in `shared/reply_emotion.py`;
Tri's idle rule (10/10: `relaxed` only, 50–60 s apart) is untouched.

**Body animation is not affected.** Emotions write morph-target weights through
`ExpressionMixer`; clips, Kimodo motion and the FSM drive bones through the
animation mixer. Nothing in this plan touches bones.

**Face-level guard — every emotion this plan adds is ≤ 0.5.** A VRM expression
can declare `overrideMouth` / `overrideBlink` / `overrideLookAt`. With `block`,
three-vrm zeroes lip-sync visemes, auto-blink or gaze while that expression is
above 0.5; with `blend` it scales them down. So a strong smile can silence the
mouth while the avatar talks, or stop the thinking glance. All new holds below
stay at or under 0.5. The reply tag's own caps (happy/relaxed up to 0.8) are
today's behaviour and not changed here.

---

## Task 0 — Measure first (backend, ~0.5 day)

Before changing behaviour, know what the reply tag actually does in production.

- [ ] In `nodes/synthesizer.py` `send_emotion()`, log one structured line per
      turn: `reply_emotion name=… intensity=… raw=… policy_override=…`, and
      `reply_emotion missing` when the stream ends with no tag.
- [ ] After 2–3 days on prod, record in the worklog: % of replies with no tag,
      distribution of names. If >20 % missing, fix the prompt before Task 3
      (the face has nothing to show otherwise).

## Task 1 — Wire the FSM's facial policy (frontend, ~1 day)

Makes "what the avatar is doing" drive the face, using data that already exists.

- [ ] `AnimationStates.ts`: extend `facial.hold` to `{ name, intensity }` so a
      state can hold an emotion softly. Set the table:

      | State | hold | Reason |
      |---|---|---|
      | idle / greeting / bored | none, `wander: true` | unchanged |
      | thinking_intro / thinking_loop | neutral 1.0 | unchanged (now applied by the policy; Task 2 replaces it per turn) |
      | thinking_outro | none | the reply's emotion can arrive during the loop; an outro hold would wipe it |
      | exercise | happy 0.35 | an encouraging coach while demonstrating |
      | gesture | happy (gesture face track still wins) | unchanged |

- [ ] In `MotionContext.tsx` where `setThinking` is called on state change, also
      call a new `AvatarController.applyStatePolicy(facialOf(state))`:
      `hold` → `expression.setEmotion(name, intensity, 500)`;
      `wander: false` → idle wanderer suspended (it already is while engaged).
- [ ] On leaving `exercise`, return to the reply's emotion if the engagement
      deadline is still live, else neutral. Store the last reply emotion on the
      controller for this.
- [ ] Guard: a state's `hold` never overrides a reply emotion received in the
      current turn, except `exercise` (which restores it on exit).
- [ ] Unit test in `AvatarController.test.ts`: entering `exercise` sets happy
      0.35; leaving restores the previous emotion; a reply emotion that arrives
      during `thinking_loop` survives `thinking_outro`.

## Task 2 — Face reacts to the query during "thinking" (backend + frontend, ~1.5 days)

The biggest visible win: the face responds within ~1–2 s of the user's message,
while the avatar is still in the thinking pose.

- [ ] Planner: add `user_affect` to its structured output, one of
      `pain | worry | sad_news | thanks | greeting | good_news | neutral`.
      Same LLM call — one more field in the schema and one line in the prompt.
      Default `neutral` on anything missing or unknown.
- [ ] `api/main.py:798`: add `extra["user_affect"]` next to `required_outputs`.
- [ ] Map affect → listening face in one table on the frontend
      (`lib/replyEmotion.ts`):

      | user_affect | face while thinking |
      |---|---|
      | pain / worry / sad_news | sad 0.3 (concern) |
      | thanks / greeting / good_news | happy 0.4 |
      | neutral | neutral (today's behaviour) |

- [ ] `ChatContext.tsx:771` (planner complete): if the turn's
      `required_outputs` contain a safety tag, force neutral (same rule as the
      reply). Otherwise call `avatar.setListeningEmotion(...)`, which replaces
      `thinking_*`'s `hold: neutral` for the rest of this thinking spell.
- [ ] The reply's own `emotion` event still overrides it when it arrives.
- [ ] Test (`replyEmotion.test.ts`): each affect maps as above; unknown → null;
      safety tag → neutral.

## Task 3 — Actions with outcomes (frontend, ~0.5 day)

Events the user can see go wrong or need their input.

- [ ] Motion `busy` / `unavailable` / render failure (`ChatContext.tsx:825-830`)
      → sad 0.25 for ~2 s ("sorry, can't show that now"), then back.
- [ ] Approval gate shown (waiting for the user to approve a motion) → relaxed
      0.4, held until they answer.
- [ ] Turn `error` (`ChatContext.tsx:868`) → sad 0.25, not a frozen neutral.

## Task 4 — Less dead time after a reply (frontend, ~0.5 day)

- [ ] When the engagement deadline passes, fade to `relaxed 0.2` instead of
      neutral (`AvatarController.ts:211`), so the resting face is warm, not blank.
      Skip if the last reply was safety content (stay neutral).
- [ ] Keep the reply emotion for `max(3 s, 1.5 s after voice)` as today — no
      change to timing.

---

## Not in this plan (deferred, needs a decision)

- **Emotion changing mid-reply** (several tags per reply, timed to the TTS audio
  of each sentence). Real value, but needs the tag stripper to work mid-stream
  and per-sentence audio alignment. Revisit after Task 0's numbers.
- **Richer expressions** (compound recipes like "concerned" = sad + relaxed,
  brow movement on speech emphasis). Model-dependent: `surprised` is a no-op on
  standard VRM 0.x models, and a model's own override flags decide what can
  combine. Needs a per-model audit of the characters in the database first.

## Done when

- Saying "my knee hurts" shows a concerned face within ~2 s, before the reply.
- Saying "thanks, that helped" shows a smile while it thinks.
- During an exercise demo the avatar smiles lightly; after it, the face returns
  to the reply's emotion.
- A red-flag / referral turn stays neutral end to end.
- While smiling at the new intensities, the mouth still lip-syncs and the eyes
  still blink.
- Unit tests pass; one manual pass on each character in the database, logged in
  `docs/worklogs/DD-MM-YYYY.md`.
