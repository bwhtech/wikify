# 0.2 / 01 — Project Hierarchy & Context

Today every Import and Source Document is global — one flat list. 0.2 introduces a
**Project** as the top-level container: you create a project, then upload documents
*into* it. A project also carries a **context prompt** that steers every LLM step and
the agent.

> **Field types are Frappe fieldtypes.** `reqd` = required; `ro` = read-only
> (system-set). Mirrors the style of [`../product/02-data-model.md`](../product/02-data-model.md).

## Entity-relationship (0.2 additions in **bold**)

```
**Wikify Project** ─1:N─▶ Wikify Import ─1:1─▶ Source Document ─1:N─▶ Source Page
      │                         │                     │
      │ context_prompt          │                     └─1:N─▶ Source Section (tree)
      │                         └─1:N─▶ Import Log Entry
      │
      └─(denormalized)──────────────────▶ Source Document.project   (for cross-doc Explore by project)
```

- A project groups **Imports**. The durable `Source Document` carries a denormalized
  `project` (copied from its Import) so the headline cross-document Explore query can
  scope to a project without a join through `Wikify Import`.
- One seeded **"Uncategorized"** project is the catch-all for backfilled + unfiled work.

---

## `Wikify Project`  (new DocType)

The container the user creates first. List-view subject for the new Projects screen.

| Field | Type | Notes |
|---|---|---|
| `naming_series` | autoname | `PRJ-.YYYY.-.#####`. |
| `project_name` | Data, reqd, unique | Display name; `title_field`. |
| `description` | Small Text | Short blurb shown on the project card. |
| `context_prompt` | Long Text | **The steering context.** Free-text: domain, audience, terminology, house style, "always prefer X over Y". Threaded into cleanup / VLM / classifier / wiki-generation prompts **and** the agent system prompt. Blank is fine. |
| `status` | Select | `Active / Archived`. Default `Active`. |
| `is_default` | Check, ro | Marks the undeletable **"Uncategorized"** catch-all (exactly one). |
| `agent_model` | Data | Optional per-project override of the agent model (else `Wikify Settings.agent_model`). |
| `import_count` | Int, ro | Denormalized count for the list card (maintained on Import insert/delete, or computed in the list API). |

- **Permissions:** same "Wikify User" role as 0.1; owner-scoped by default.
- **Guards (`Wikify Project` controller):** `on_trash` blocks deleting `is_default` or any
  project that still has Imports ("move or delete its imports first"). `validate` keeps
  `project_name` unique and prevents a second `is_default`.

---

## Changes to existing DocTypes

### `Wikify Import` — add

| Field | Type | Notes |
|---|---|---|
| `project` | Link → Wikify Project, **reqd** | The owning project. Defaults to "Uncategorized" if the create dialog doesn't set it. Indexed. |

### `Source Document` — add

| Field | Type | Notes |
|---|---|---|
| `project` | Link → Wikify Project, ro | Denormalized from `Wikify Import.project` at parse time (when the Source Document is created). Indexed — drives the project-scoped Explore. |

> `Source Section` and `Source Page` reach their project via `source_document.project`;
> no link is added to them (keeps the tree/page rows lean — same reasoning that dropped
> the POC `edges` table).

---

## Seed + backfill (migration)

A single post-model-sync patch, alongside the existing
`wikify.patches.v1_0.seed_section_types`:

`wikify/patches/v0_2/seed_uncategorized_and_backfill.py`

```
1. Ensure a "Uncategorized" Wikify Project exists (is_default=1). Idempotent
   (get-or-create by is_default).
2. UPDATE every Wikify Import with no project   → Uncategorized.
3. UPDATE every Source Document with no project  → its Import's project
   (= Uncategorized for all existing rows).
```

Register in `patches.txt` under `[post_model_sync]`. Re-running is a no-op (filters on
empty `project`). Also seed "Uncategorized" in `install.py` / `after_install` so fresh
sites have it before the first Import.

---

## Where the context prompt threads

`context_prompt` is the project's one steering lever. Resolve it once per job from
`import.project.context_prompt` and pass it down as an optional `project_context: str`
argument — **prepended as a clearly-delimited block** to the relevant system/user
prompt. No logic changes beyond prompt assembly; blank context = current behavior.

| Step | Module | How the context is used |
|---|---|---|
| Markdown cleanup | `engine/loader/cleanup_llm.py` | Prepend `"Project context:\n{context}\n\n"` to the cleanup instruction so terminology/house-style is respected. |
| VLM re-parse | `engine/parsers/vlm.py` | Same block in the vision prompt (e.g. "this is a surgical manual; label anatomical diagrams precisely"). |
| Classification | `engine/loader/classifier.py` | Append context after the taxonomy so the classifier reads domain hints. |
| Wiki generation | `engine/loader/wiki.py` | Context available for any title/intro normalization (light touch — generation is mostly structural). |
| **Agent** | `wikify/agent/loop.py` | Injected into the system prompt whenever a project is in scope (see [`02-ai-agent.md`](02-ai-agent.md)). |

Thread the value through the **jobs** (`jobs/parse.py`, `jobs/remediate.py`,
`jobs/classify.py`, `jobs/generate.py`), which already load the Import — read
`project_context` there and pass into the engine calls. Keep `engine/` functions pure
(string in, string out); don't have the engine read DocTypes for context.

---

## Frontend

### Routes (added)

| Path | Page |
|---|---|
| `/wikify` | **Projects list** (new home) — replaces the flat Imports list as the landing screen. |
| `/wikify/project/:name` | Project detail — its Imports list + project actions. |
| `/wikify/project/:name/settings` | Project settings (name, description, **context prompt**, agent model, archive). |
| `/wikify/import/:name/:tab?` | Import detail — unchanged (now shows a project breadcrumb). |
| `/wikify/explore` | Global Explore — gains a **project filter** (default: all projects). |

> The 0.1 flat Imports list (`pages/ImportList.vue`) becomes the **project-scoped**
> imports list rendered inside Project detail (same component, `filters={project}`).

### Screens

- **Projects list** (`pages/ProjectList.vue`) — `useList("Wikify Project")` as cards:
  name, description, import count, status. Header **"New Project"** (`dialog.prompt` or a
  small `Dialog` with name + description). Card click → project detail. "Uncategorized"
  is pinned/labelled.
- **Project detail** (`pages/ProjectDetail.vue`) — header with project name +
  breadcrumb (Projects → {name}), a **"New Import"** button (the 0.1 dialog, with
  `project` preset to this project), and the imports list filtered to this project.
- **Project settings** (`components/ProjectSettings.vue`) — a `FormControl` form:
  `project_name`, `description`, a **textarea for `context_prompt`** (with helper copy:
  "Passed to every AI step and the assistant"), `agent_model`, Archive toggle. One solid
  primary "Save".
- **New Import dialog** — add a **Project** picker (Link/Autocomplete to `Wikify
  Project`), defaulting to the current project (if launched from project detail) or
  "Uncategorized" (if launched globally).
- **Sidebar** (`AppShell.vue`) — nav becomes **Projects** + **Explore** (drop the
  top-level "Imports" entry; imports live under a project now).

### API (added / changed)

`wikify/api/projects.py`
- `create_project(project_name, description="")` → insert + return name.
- `list_projects()` → projects with `import_count` (or rely on `useList` + a count).
- `archive_project(name)` / `update_project_context(name, context_prompt, ...)` (or just
  `useDoc` setValue on the form).

`wikify/api/imports.py` — `start_import` gains a `project` param (defaults to the
"Uncategorized" project name when omitted); stamps it on the Import and (at parse) on the
Source Document.

`wikify/api/explore.py` — accept an optional `project` filter on the cross-document
query.

---

## Acceptance

- Creating a project, then a New Import inside it, files the Import (and its eventual
  Source Document) under that project; the Projects list shows the count.
- All pre-0.2 Imports/Source Documents appear under **"Uncategorized"** after migrate;
  none are orphaned.
- Editing a project's **context prompt** then re-running cleanup / a VLM re-parse visibly
  reflects the context (e.g. terminology preference) — verified headless by diffing
  output with vs. without context.
- Deleting "Uncategorized", or a project that still owns Imports, is blocked with a clear
  message.
- Global Explore can be filtered to a single project and still spans documents within it.
