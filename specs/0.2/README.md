# Wikify 0.2 — Projects, Agent, Wiki Preview

Version **0.1** delivered the spine: PDF → parse → score → remediate → tree → classify
→ Explore → **Wiki generation** (v0.1 slices 1a–9, see
[`../product/IMPLEMENTATION-PLAN.md`](../product/IMPLEMENTATION-PLAN.md)). Everything
in 0.1 is **global and flat** — one big list of Imports — and the only intelligence the
user can steer is the per-page review.

**0.2 adds three capabilities, in this delivery order:**

1. **Project hierarchy** — group imports under a **Project**. A project owns a
   **context prompt** ("this is a neonatal ICU manual; prefer clinical terminology…")
   that flows into every LLM step *and* into the agent. Existing global imports are
   backfilled into a seeded **"Uncategorized"** project. → [`01-project-hierarchy.md`](01-project-hierarchy.md)

2. **AI agent** — a chat assistant that sits on every screen and *does the work with
   you*: read and rearrange the section tree, retag sections, re-classify, and
   **re-parse a page or document from a plain-English instruction** ("don't make this a
   mermaid diagram — just embed the page image"). Built on **litellm + OpenRouter** with
   a background-job tool-calling loop streaming over realtime — architecture adapted
   from Frappe Builder's `ai-session` branch, **simplified** (no canvas, no artifact
   model, mostly server-side tools). → [`02-ai-agent.md`](02-ai-agent.md)

3. **Wiki rendered preview** — in the Tree / Wiki tab, clicking a node shows the page
   **rendered as it will look in the actual wiki** (not raw markdown), framed like a
   real wiki page, *before* generating. → [`03-wiki-preview.md`](03-wiki-preview.md)

## How 0.2 relates to 0.1

0.2 is **additive** — it does not re-architect the pipeline. The DocTypes, jobs, and
APIs from 0.1 stay; 0.2 adds a `project` link to the existing chain, threads a context
string through the existing prompts, and adds a new `wikify/agent/` package + two chat
DocTypes alongside (not inside) the pipeline. The wiki preview reuses the existing
`MarkdownPreview.vue` + the wiki app's markdown renderer.

| 0.2 area | New DocTypes | New backend | New frontend |
|---|---|---|---|
| Projects | `Wikify Project` | `project` link + backfill patch; context threaded into `engine/loader/cleanup_llm`, `parsers/vlm`, `loader/classifier`, `loader/wiki` | Projects list + detail, project picker in New Import, project settings form, Explore scoped by project |
| Agent | `Wikify Agent Session`, `Wikify Agent Message` | `wikify/agent/` (litellm loop, tool registry, session facade), `api/agent.py`, realtime channels | `AgentChatPanel.vue` + controller, context-attachment store, tool-call cards |
| Wiki preview | — | `api/wiki.render_section_preview` (reuse wiki markdown renderer) | `WikiPreview.vue` (wiki-fidelity frame) |

## Spec index

| Doc | Covers |
|---|---|
| [`01-project-hierarchy.md`](01-project-hierarchy.md) | `Wikify Project` DocType, the `project` link + denormalization, the "Uncategorized" seed + backfill patch, the **context prompt** and where it threads, project UI. |
| [`02-ai-agent.md`](02-ai-agent.md) | Agent architecture (litellm loop, `side`-tagged tool registry), the two chat DocTypes, the tool catalogue, context attachment, whitelisted API, realtime contract, the chat panel + controller. |
| [`03-wiki-preview.md`](03-wiki-preview.md) | Rendered wiki-fidelity preview of a section/tree before generation; renderer reuse; page-ref resolution in preview. |
| [`IMPLEMENTATION-PLAN.md`](IMPLEMENTATION-PLAN.md) | Tracer-bullet slices **10–16** (continuing 0.1's spine), delivery order, and per-slice Verify steps against `pdf.localhost`. |

## Conventions (unchanged from 0.1)

Same as [`../product/README.md`](../product/README.md): backend follows the
`frappe-app-dev` skill (DocTypes per the data-model docs, jobs in `wikify/jobs/`, thin
whitelisted APIs in `wikify/api/`); frontend is **frappe-ui v1** with `useCall`/`useList`/
`useDoc`, semantic tokens only, one solid primary action per page. **Verify each slice
against `pdf.localhost` before starting the next.** Work directly on `main`.

## Decisions locked (2026-06-22)

- **Agent LLM stack: litellm** (new dependency) — gives streaming, retries, fallback
  chains, prompt-caching, and tool-call accumulation for free, matching the Builder
  reference. The existing `engine/llm.py` (requests-based, pipeline scoring) **stays as
  is** — only the agent uses litellm. Both hit OpenRouter with the same key resolver
  (`engine.settings.openrouter_key`).
- **Project is required**, but a seeded **"Uncategorized"** project (`is_default=1`,
  undeletable) is the catch-all; a post-model-sync patch backfills every existing Import
  and Source Document into it. New Imports default to "Uncategorized" if the user
  doesn't pick one.
- **Delivery order: Projects → Project context → Agent → Wiki preview.** The agent
  depends on projects for its context, so projects land first.
