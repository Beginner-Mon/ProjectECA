---
name: Weekly exercise plan
description: Build a 7-day exercise plan from ECA's knowledge base and ask for the details that are missing. Use when the user asks for a weekly training schedule or a multi-day plan.
---

# Building a 7-day exercise plan

Work through these steps in order. Stay inside ECA's knowledge base: every
exercise on the plan must come from a `kb_search` result.

1. Decide the goal and the working constraints:
   - what the user wants (for example knee strength, posture, general fitness);
   - how many days per week they can train (default to 3 if not stated);
   - equipment, pain or diagnosis limits. If unsure, check the user's saved
     details at the top of the conversation before asking.
2. Collect exercises:
   - one focused `kb_search` per category you need (warm-up, main exercises,
     cool-down / stretches);
   - if a search returns nothing usable, try one rephrase of the query; if
     that still fails, tell the user that part is not in the library yet.
3. Build the plan:
   - put the hardest sessions on separate days, alternate them with lighter
     or rest days;
   - for each day write the focus and 2–4 exercises with sets × reps and
     rest between sets — only numbers the KB states.
4. Missing details: if days, equipment or limits are still unknown after
   step 1, produce a conservative draft plan anyway and then ask the user to
   confirm the constraints so the plan can be refined in the next turn.
5. Stop the turn when the plan is on paper, plus any question for the user.

Format each day as: `Day N: <focus> — <exercise> (sets × reps), …`.
Prefer low-impact choices by default, flag anything the user's memory says
is contraindicated, and never schedule two sessions on the same day.
