# Spec loop — one tracer-bullet slice per iteration

You are working in the **Wikify** app (`apps/wikify`) — PDFs → reviewed, typed, navigable
Frappe Wiki spaces. Read and obey `CLAUDE.md` at the app root — its Dev environment,
Conventions, and **Git** sections are authoritative. Always load and use the
**frappe-app-dev** skill (and **code-style** when writing code, **frappe-ui** when
touching `frontend/**`).

## CONTEXT (passed in at the top of your prompt)

- The implementation plan to drive, the current branch, and recent commits. You are
  **already on the correct branch (`main`)** — do NOT create or switch branches. Wikify
  commits work directly on `main`; **do not push** (push only when the user asks).

## SOURCE OF TRUTH = THE PLAN'S SLICE MAP

The plan (default `specs/0.2/IMPLEMENTATION-PLAN.md`) has a **Slice map** table with a
`Status` column, and each slice has its own `## N — …` section with **Acceptance
criteria** checkboxes. This — not git history — is how you know what is done.

1. Read the whole plan.
2. In the Slice map, find the **first slice whose `Status` is `—`** (not done) **whose
   `Blocked by` slices are all done**. Slices are ordered so each builds on the previous —
   never skip ahead past an unmet dependency.
3. That slice is your task for this iteration. Also read the area-spec files it references
   under "Spec refs" (e.g. `01-project-hierarchy.md`, `02-ai-agent.md`,
   `03-wiki-preview.md`) — those carry the per-feature detail.

If every slice in the Slice map is done: output `<promise>COMPLETE</promise>` and stop.

## ONE SLICE PER ITERATION

Work on **exactly ONE slice.** A slice is already the smallest end-to-end vertical cut
(DocType → `engine/`/`agent/` → job → API → realtime → SPA); do not split it across
iterations, and do not bundle two.

If the chosen slice turns out larger than the plan implies (e.g. it needs a refactor
first), output `HANG ON A SECOND`, carve off the smallest prerequisite chunk, do only
that, and record the split in the plan (add a sub-slice row + note). Don't outrun your
headlights.

## EXPLORATION

Explore first — the DocTypes, the `engine/` and `agent/` packages, `wikify/jobs/`,
`wikify/api/`, and the `frontend/src/**` components named in the slice and its area spec.
**Reuse before writing new code** (the pipeline, the tree APIs, `MarkdownPreview.vue`, the
existing realtime/job plumbing). Port logic from `scratch/pdf_lab/` unchanged where a
slice references the POC.

## EXECUTION

Implement the slice end-to-end through every layer it touches, following the slice's +
area spec's detail. Run all `bench` commands **from the bench root**
(`/Users/mdhussain/Frappe/benches/december-bench`); the dev site is **`pdf.localhost`**.

- After any DocType / schema / patch change: `bench --site pdf.localhost migrate`.
- After touching anything under `frontend/src/**`: build it (`bench build --app wikify`,
  or `yarn build` from `frontend/`) — do not wait to be asked.
- Background jobs + realtime + the agent need `bench start` (web + socketio + workers)
  running to exercise end-to-end.

## FEEDBACK LOOPS (before committing) — the plan's Verification protocol

Follow the plan's **Verification** section, in order:

1. **Headless first** — `bench --site pdf.localhost execute <dotted.path>` or
   `bench --site pdf.localhost console`; assert the rows/fields/status the slice produces.
   For agent slices, exercise `AgentRunner.run()` headlessly (mock or live model) before
   the UI.
2. **Automated** — add/extend `FrappeTestCase` tests under
   `wikify/wikify/doctype/**/test_*.py` or `wikify/tests/`; run
   `bench --site pdf.localhost run-tests --app wikify` (scope to the new module where
   possible). When fixing a bug, temp-revert the fix first to confirm the test fails, then
   restore it. Mock litellm for agent unit tests.
3. **UI walkthrough** — at `/wikify` as **Administrator / admin**, reproduce the slice's
   demo and confirm realtime (progress/log/stream) where applicable. Use the
   **agent-browser** skill (or the `/verify` skill) against `pdf.localhost` — NEVER launch
   chromium directly. Capture a screenshot of the working slice.
4. **Regression** — re-run the prior slice's demo and a v0.1 end-to-end parse to confirm
   the spine still works.

Keep `pre-commit` (ruff/eslint/prettier/pyupgrade) green.

## RECONCILE THE PLAN

Three updates to the plan file (and area spec if it deviated), committed with the code:

1. Add a short **As built** note to the slice's `## N — …` section (what shipped, any
   deviation), in the same style as the v0.1 "As built" notes.
2. Tick the slice's **Acceptance criteria** checkboxes: `- [x] …`.
3. In the **Slice map**, set the slice's `Status` to `✅` (and update any "Progress" /
   "Up next" note line in the plan).

## COMMIT

One **proper conventional commit** on `main` (no special prefix). Examples:
`feat(projects): Wikify Project doctype + backfill patch + project/imports UI (slice 10)`
or `feat(agent): walking-skeleton chat loop with read_tree tool + streaming (slice 12)`.
Body: key decisions, files-changed summary, and any note for the next iteration. Reference
the plan + slice number. Keep it concise. **Commit only — never create a branch, never
push** (the user pushes when ready).

## NOTIFY (optional)

If `bwh_bot` is available (`bwh_bot --help`), send a short summary of the slice shipped
with the agent-browser screenshot(s) attached. Skip silently if it isn't.

## FINAL RULES

- ONE slice per iteration. Never create, switch, or push branches — work on `main`, commit
  locally.
- Reuse existing helpers; match surrounding code style; semantic tokens only on the
  frontend.
- If blocked, leave the slice's `Status` as `—`, record the blocker in the commit message,
  and stop.
