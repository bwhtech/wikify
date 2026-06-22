# 0.2 / 02 — AI Agent

A chat assistant that sits on every Wikify screen and **does the conversion work with
you**. It can read the section tree, the tags (Section Types), and page/section content;
it can rearrange and retag the tree, re-classify, and **re-parse a page or document from
a plain-English instruction** ("don't make this a mermaid diagram — just embed the page
image"). It is **context-aware**: opened globally it's global; opened inside a project /
document / page / section, that thing is attached by default (removable, like file chips
in Claude).

Architecture is adapted from Frappe **Builder's `ai-session` branch** and deliberately
**simplified**: no canvas, no heavy "artifact model", no working-tree mirror. Most tools
run server-side and return text; a few enqueue existing pipeline jobs.

## Stack

- **litellm** (new dependency, `pyproject.toml`) — the only LLM client for the agent.
  `litellm.completion(model="openrouter/<...>", messages, tools, stream=True,
  api_key=...)`. Set `litellm.drop_params = True`. The existing requests-based
  `engine/llm.py` (pipeline scoring) is untouched — the agent has its own adapter.
- **OpenRouter** — same key resolver as the pipeline
  (`engine.settings.openrouter_key()`); models are `openrouter/`-prefixed.
- **Frappe realtime** (`frappe.publish_realtime`) — token + tool-call streaming, exactly
  as the pipeline streams `wikify_import_progress`/`_log` today.
- **Background job** (`frappe.enqueue`, `queue="long"`) — the agent loop runs off the web
  worker; the `run()` API returns `202` immediately and the answer streams over realtime.

## Package layout — `wikify/agent/`

```
wikify/agent/
  llm.py        # litellm adapter: complete_with_tools(model, messages, tools, *, stream, api_key)
  registry.py   # Tool dataclass + build_default_registry()
  loop.py       # AgentRunner.run() — the round loop; streams + persists messages
  session.py    # facade over the two DocTypes (get_or_create, append_message, build_context_messages, set_running)
  context.py    # resolve attachments → context block + scoped tool availability + project context_prompt
  prompts.py    # system prompt template
  tools/
    read.py     # read_tree, read_section, read_page, list_section_types, search_sections
    tree.py     # move_section, rename_section, set_section_type, toggle_include_in_wiki
    taxonomy.py # create_section_type
    reparse.py  # use_page_image, reparse_page, reparse_document
    pipeline.py # reclassify, regenerate_wiki
    converse.py # ask_clarification
wikify/jobs/agent.py   # run_agent_job(session, message_id, ...) → AgentRunner(...).run()
wikify/api/agent.py    # whitelisted: run / cancel / get_session / list_sessions / new_session / clear_session
```

---

## DocTypes

Two DocTypes — a session and standalone messages linked to it (messages are **not** a
child table, so the job appends cheaply and one row streams without rewriting the
parent — same reasoning as `Import Log Entry`).

### `Wikify Agent Session`

| Field | Type | Notes |
|---|---|---|
| `naming_series` | autoname | `AGT-.YYYY.-.#####`. |
| `title` | Data | Auto-summarized from the first user message (truncated); editable. |
| `user` | Link → User, ro | Owner. |
| `scope` | Select | `global / project / document / page / section` — the *primary* context the session opened in. |
| `project` | Link → Wikify Project | Set when scope ≥ project (drives the injected context prompt). |
| `source_document` | Link → Source Document | Set when scope ≥ document. |
| `model` | Data | Resolved agent model for this session (project override → settings). |
| `status` | Select | `Active / Archived`. |
| `is_running` | Check, ro | Concurrency guard — `run()` rejects if already running. |
| `last_interaction_on` | Datetime, ro | For session-list ordering. |

> Per-message *attachments* (the removable context chips) are stored on the message, not
> the session — the user can change attachments turn to turn (attach a different page
> mid-conversation). The session's `scope`/`project`/`source_document` are just the
> defaults the panel opened with.

### `Wikify Agent Message`

`autoname: hash`, sorted by `creation asc`.

| Field | Type | Notes |
|---|---|---|
| `session` | Link → Wikify Agent Session, reqd | Indexed (`search_index`). |
| `role` | Select | `user / assistant / tool`. |
| `content` | Long Text | Message text (assistant: the streamed answer; tool: result summary). |
| `tool_calls` | Long Text (JSON) | Assistant turn's requested tool calls `[{id, name, args}]`. |
| `tool_name` / `tool_call_id` | Data | For `role=tool` rows, links the result to its call. |
| `attachments_json` | Long Text (JSON) | `[{type, name, label}]` — the context chips at send time (user rows). |
| `status` | Data | `streaming / done / error / clarification` — hoisted out of metadata for cheap filtering. |
| `metadata_json` | Long Text (JSON) | Cost/latency/model, applied-change summaries, clarification options, etc. |

A patch is **not** needed (new doctypes). `on_trash` of a Session cascades its Messages.

---

## The agent loop (`agent/loop.py`)

`AgentRunner(session, user, attachments, model).run()` — one class, per-turn state:

```
build messages:
  [ system prompt (+ project context_prompt if in scope) ]
  [ attachment context block  (resolved tree/doc/page/section summaries) ]
  [ last N turns from Wikify Agent Message ]
  [ this user message ]
for _round in range(MAX_ROUNDS):                      # MAX_ROUNDS ~ 25
    stream a tool-calling completion (litellm, stream=True):
        - text deltas        → emit wikify_agent_stream {chunk}
        - tool_call deltas   → accumulate by index (Builder's pattern)
    if no tool calls:                                  # model answered → turn ends
        persist assistant message (status=done); emit wikify_agent_complete; return
    for each tool call, by registry `side`:
        terminal (ask_clarification)  → persist + emit wikify_agent_clarify; return
        server   (read/query/action) → run handler(ctx, args) now;
                                        emit wikify_agent_tool {name, args, summary};
                                        feed result back as a `tool` message; continue loop
    (cancel check each chunk via Redis flag wikify_agent_cancel:<session>)
```

- **No `client` side.** Builder splits tools into client (browser applies to canvas) /
  server / terminal; Wikify has no live canvas, so **everything is `server` or
  `terminal`**. A tool that mutates a DocType does it directly and returns a confirming
  summary string; the frontend just re-fetches the affected resource (`useDoc`/`useList`
  auto-refetch) — no op-replay layer.
- **Retries:** wrap the streaming round (litellm `num_retries`, plus a manual 3× retry on
  transient stream errors — streaming disables litellm's fallback chain, as in Builder).
- **Cancel:** `api.cancel` sets `wikify_agent_cancel:<session>` in Redis; the loop checks
  it per chunk and closes cleanly.
- **No-op guard (optional, port if needed):** if the model narrates an edit ("I've moved
  the section…") without calling a tool, spend one corrective round — Builder's
  `claims_unbacked_action`.

---

## Tool catalogue

Tools are plain `Tool` dataclass instances (Builder's pattern — **no decorator**):

```python
@dataclass
class Tool:
    name: str
    side: Literal["server", "terminal"]
    description: str
    parameters: dict                       # raw JSON-schema for the function args
    handler: Callable[[Ctx, dict], str]    # runs it; returns a result/summary string
```

`build_default_registry()` collects the module-level `TOOLS` lists. **Adding a capability
= registering one Tool; the loop never changes.** Handlers call **existing** `api/` and
`jobs/` functions — they do not re-implement pipeline logic.

### Read / context tools (`tools/read.py`)

| Tool | Args | Does |
|---|---|---|
| `read_tree` | `source_document?` | Returns the Source Section tree (title, type, page range, hierarchy) as compact YAML. Defaults to the attached document. Reuses `api.sections.get_tree`. |
| `read_section` | `name` | A section's markdown + metadata. |
| `read_page` | `source_document?`, `page_no` | A page's `canonical_markdown` + verdict + scores. |
| `list_section_types` | — | The taxonomy (Section Types: name, label, description, color). |
| `search_sections` | `section_type?`, `query?`, `project?` | Explore-style cross-document lookup. Reuses `api.explore`. |

### Write / tree tools (`tools/tree.py`, `tools/taxonomy.py`)

| Tool | Args | Does |
|---|---|---|
| `move_section` | `name`, `new_parent`, `new_index` | Reparent/reorder. Reuses `api.sections.reorder_section` (keeps `lft/rgt`/`level`/`hierarchy_path` consistent). |
| `rename_section` | `name`, `title` | Rename a section. |
| `set_section_type` | `name`, `section_type` | **Retag** a section (the "tags" capability). |
| `toggle_include_in_wiki` | `name`, `include` | Drop/restore a section from generation. |
| `create_section_type` | `type_name`, `label`, `description?`, `color?` | Extend the taxonomy when the user wants a new tag. |

### Re-parse tools (`tools/reparse.py`) — the headline capability

| Tool | Args | Does |
|---|---|---|
| `use_page_image` | `source_document?`, `page_no` | **Deterministic** — replaces a page's `canonical_markdown` with an embed of its rendered image (`![Page N](<image File url>)`), `canonical_source="image"`. The literal "just paste the image of the pdf" request — no LLM. |
| `reparse_page` | `source_document?`, `page_no`, `method` (`vlm`/`cleanup`), `instruction` | Enqueue a **single-page** re-parse, passing `instruction` into the VLM/cleanup prompt (atop the project context). Re-scores and updates canonical, like the remediate job but page-scoped + instruction-steered. |
| `reparse_document` | `source_document?`, `instruction` | Same, document-wide (enqueues a remediate-style job with the instruction). Confirm before running — expensive. |

### Pipeline tools (`tools/pipeline.py`)

| Tool | Args | Does |
|---|---|---|
| `reclassify` | `source_document?` | Re-run classification (re-tag the whole tree). Reuses `jobs/classify.py`. The "re-index the tags" capability. |
| `regenerate_wiki` | `source_document?` | Re-run idempotent wiki generation. Reuses `jobs/generate.py`. Confirm first. |

### Conversational (`tools/converse.py`)

| Tool | Args | Does |
|---|---|---|
| `ask_clarification` | `question`, `options?` | **terminal** — ends the turn asking the user; renders as option chips in the panel. |

> **Expensive/destructive ops** (`reparse_document`, `regenerate_wiki`, full
> `reclassify`) should **confirm in the UI before executing**. Implement as: the tool
> returns a "needs confirmation" sentinel that the panel renders as a confirm card, and a
> follow-up `run()` with the user's approval re-invokes it — or gate at the panel by
> intercepting these tool names. Page-scoped edits (`move_section`, `set_section_type`,
> `use_page_image`, single `reparse_page`) apply directly (cheap, reversible by re-edit).

---

## Context attachment (the "@-mention / file chips" model)

What the agent knows about *what you're looking at* comes from **attachments** sent with
each `run()` — `[{type, name}]` where `type ∈ {project, document, page, section}`.

**Defaults by where the panel is opened** (the frontend `agentContext` store, updated by
each page):

| You are on… | Default attachment |
|---|---|
| Projects list / anywhere global | none (scope `global`) |
| A **project** screen | that **project** |
| An **import / document** detail | that **Source Document** (+ its project) |
| **Pages** tab with a page selected | that **Source Page** (+ document + project) |
| **Tree** tab with a section selected | that **Source Section** (+ document + project) |

Each default chip is **removable** (the ✕, like Claude). Resolution (`agent/context.py`):

- Attachments expand into a **context block** prepended to the messages — e.g. the
  document's tree outline, the selected section's markdown, the page's canonical markdown.
  Keep it bounded (outline + the focused item's body; the agent pulls more via `read_*`
  tools on demand — Builder's skeleton-context idea).
- The attached **project's `context_prompt`** is injected into the system prompt.
- Attachments also **scope tool defaults** — `read_tree`/`reparse_page` etc. default
  `source_document` to the attached document, so the user rarely names ids.

---

## Whitelisted API (`wikify/api/agent.py`)

Each `@frappe.whitelist()`:

| Endpoint | Args | Returns |
|---|---|---|
| `run` | `prompt`, `session_id?`, `scope`, `project?`, `source_document?`, `attachments[]`, `model?` | Validates; `get_or_create` session; rejects if `is_running` (429); appends the user message; `frappe.enqueue(run_agent_job, queue="long")`; returns `202 {session_id, message_id}`. The answer arrives via realtime. |
| `cancel` | `session_id` | Sets `wikify_agent_cancel:<id>` in Redis; loop stops at next chunk. |
| `get_session` | `session_id` | Session + its messages (ordered) for hydration on open. |
| `list_sessions` | `scope?`, `project?`, `source_document?` | The user's sessions for the history dropdown. |
| `new_session` | `scope`, `project?`, `source_document?` | Explicit fresh session. |
| `clear_session` / `delete_session` | `session_id` | Archive / delete. |
| `get_agent_models` | — | Allowed models for the picker (from `Wikify Settings`). |

## Realtime contract

All events suffixed by session id and sent to the requesting `user` (mirrors Builder):

| Event | Payload | Frontend effect |
|---|---|---|
| `wikify_agent_stream:<sid>` | `{message_id, chunk}` | Append token to the streaming assistant bubble. |
| `wikify_agent_tool:<sid>` | `{name, args, status, summary}` | Render/append a tool-call card (running → done). |
| `wikify_agent_clarify:<sid>` | `{message_id, question, options}` | Render a clarification card with option chips. |
| `wikify_agent_complete:<sid>` | `{message_id, usage}` | Finalize the bubble; re-fetch affected resources. |
| `wikify_agent_error:<sid>` | `{message}` | Show error state + retry. |

On `complete`, the controller **re-loads the session from the DB** so persisted state is
the source of truth (Builder's pattern), and triggers `useDoc`/`useList` refetch of any
resource the tools mutated (tree, page, section).

---

## Frontend

- **`components/AgentChatPanel.vue`** — a slide-over panel (right side) mounted once in
  `AppShell.vue`, toggled by a **floating chat button** present on every screen. Contains:
  message list (chat bubbles, **tool-call cards**, clarification cards), the input box
  (textarea + model picker + send/stop), and the **context chips row** (attachments with
  ✕). A session-history dropdown.
- **`composables/useAgentChat.js`** (or a small controller class à la Builder's
  `AIChatController`) — reactive `messages`, `prompt`, `sessionId`, streaming
  accumulators; `submitPrompt`, `cancel`, `loadSession`, `selectClarifyOption`. Calls
  `wikify.api.agent.run`, optimistically pushes the user bubble + a "Thinking…" assistant
  bubble, then lets realtime drive.
- **`data/agentContext.js`** — a reactive store the pages write to (`setProject`,
  `setDocument`, `setPage`, `setSection`, `clear`) and the panel reads for default chips.
  `ImportDetail`/`PageReview`/`SectionTree`/`ProjectDetail` call it on mount/selection.
- **`agent/realtime.js`** — subscribe the five `wikify_agent_*:<sid>` events through the
  existing `socket.js`; map to controller handlers (socket events, **not** polling).

Follow the frappe-ui skill: semantic tokens, `Button`/`Dialog`/`Badge`/`Spinner`, one
primary send action. Keep it **simpler than Builder** — chat + tool cards, no canvas.

---

## Acceptance

- A floating chat button opens the panel on any screen; asking "summarize this document's
  tree" while on an import streams an answer after the agent calls `read_tree`.
- Opening the panel on a project attaches the project (chip removable); the project's
  context prompt visibly steers answers.
- "Move the 'Anaesthesia' section under 'Clinical Protocols'" reorders the tree and the
  Tree tab reflects it on refetch.
- "Tag pages 40–45 as surgical_procedures" retags those sections.
- "Don't make page 7 a mermaid diagram — just embed the page image" runs `use_page_image`
  and the page's canonical markdown becomes the image embed.
- "Re-parse page 12 and keep the table as a real markdown table" enqueues an
  instruction-steered `reparse_page`; the result updates canonical.
- Expensive ops (reparse whole document / regenerate wiki) ask for confirmation first.
- `cancel` stops a running stream; a second concurrent `run` on the same session is
  rejected.
