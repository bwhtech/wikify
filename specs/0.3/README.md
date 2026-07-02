# Wikify 0.3 — Agent Wiki Editing

Version **0.2** gave the agent read + tree + re-parse powers (slices 10–16). But the
"Test Edit" incident (2026-07-02, session `AGT-2026-00167`) exposed the gap: a user
asked the agent to fix a broken table on the REVISION HISTORY wiki page, the agent
re-parsed six pages, declared success — and **the wiki preview didn't change**, because
every content layer the user actually *sees* sits downstream of the one the agent can
write.

The content chain:

```
Source Page.canonical_markdown ──(sectionize)──▶ Source Section.markdown ──(generate)──▶ Wiki Document
        agent can write ✅                        preview renders this ❌               real wiki ❌
```

**0.3 closes the chain.** The user should be able to point at anything in the wiki
preview or the generated wiki and say "fix this" — and the agent should be able to fix
it *at the layer where it lives*, propagate it forward, and verify the fix at the layer
the user is looking at.

## Spec index

| Doc | Covers |
|---|---|
| [`01-agent-editing-tools.md`](01-agent-editing-tools.md) | The full tool audit, the layer model, ten new/changed tools (content edit, propagation, wiki sync, structure surgery, rendered-layer reads), guardrails, honesty fixes to existing tools, and the **live-agent eval harness** (real-model scenarios asserting on DB outcomes). |
| [`IMPLEMENTATION-PLAN.md`](IMPLEMENTATION-PLAN.md) | Tracer-bullet slices **17–20** (continuing 0.2's numbering), delivery order, per-slice Verify steps against `pdf.localhost`. |

## Principles locked (2026-07-02)

1. **Source layer is the single source of truth.** All content edits land on
   `Source Section.markdown` (or `Source Page.canonical_markdown`), never directly on
   `Wiki Document` — a direct wiki edit would be silently wiped by the next
   `regenerate_wiki`. Wiki pages update only via sync/generate.
2. **Every write tool propagates or says it didn't.** A tool that changes an upstream
   layer must either push the change forward automatically (when unambiguous) or state
   in its result string exactly which downstream layers are now stale and which tool
   refreshes them. No more `"Canonical updated."` half-truths.
3. **Verify at the layer the user sees.** After a content mutation the agent verifies
   with the rendered-preview read tool (what the user's screen shows), not by re-reading
   the layer it just wrote.
4. **Structure surgery is cheap and reversible where possible, confirm-gated where
   not.** Rename/move/split/merge apply directly; delete and document-wide operations
   hold for a UI confirm card (existing Slice 14 mechanism).

## Conventions (unchanged)

Same as [`../0.2/README.md`](../0.2/README.md): backend per the `frappe-app-dev` skill,
handlers call existing `api/` + `engine/` functions, frontend frappe-ui v1, verify every
slice against `pdf.localhost` before the next, work directly on `main`.
