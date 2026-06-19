# 04 — Frontend Plan (Frappe UI SPA)

Phase-by-phase frontend work. Scaffold adapted from **frappe/gameplan**'s frontend
(the live **frappe-ui v1 / `1.0.0-beta.10`** reference — the bench's CRM is on an
older `0.1.x` and was not used). Follow the bundled **frappe-ui skill** (`skills/frappe-ui/`:
SKILL · COMPONENTS · PATTERNS · TOKENS · SETUP) for layout, tokens, and data-fetching
conventions; authoritative component index at `ui.frappe.io/llms.txt`. All UI comes
from frappe-ui except two pieces it doesn't ship: a **resizable split-pane**
(`splitpanes`) and a **drag-rearrange tree** (`vuedraggable`).

**Layout conventions (from the skill's PATTERNS/TOKENS):** app shell =
`FrappeUIProvider` + `Sidebar` + `<router-view>`; every page has a 48px (`min-h-12`)
sticky header with `Breadcrumbs` + actions; body wrapped in a `body-container`
(`mx-auto max-w-[940px] px-3 sm:px-5`); **semantic tokens only** (`bg-surface-*`,
`text-ink-*`, `border-outline-*` — never raw gray); color via `variant` + `theme`;
exactly one `variant="solid" theme="gray"` primary action per page; verify dark mode
via `[data-theme="dark"]`.

## Scaffold (Phase 0)

```
wikify/frontend/
  src/
    main.js            # createApp + router + FrappeUI + initSocket; dev pulls boot
    router.js          # createWebHistory(__FRONTEND_ROUTE__ + '/'), beforeEach auth guard
    socket.js          # initSocket + refetch_resource listener
    data/              # reactive stores: session (cookie-based), currentImport
    utils/             # useTheme (light/dark/system) + helpers
    pages/             # route components
    components/        # AppShell (sidebar + dark toggle) + screen-specific components
  vite.config.js       # frappeui({ frontendRoute: '/wikify' }) + vue
wikify/www/wikify.py   # get_context() Guest→login gate + boot; get_context_for_dev (dev)
wikify/www/wikify.html # build artifact — emitted by the vite plugin (gitignored)
```

`hooks.py`: `website_route_rules = [{"from_route": "/wikify/<path:app_path>",
"to_route": "wikify"}]`, `app_icon_route = "/wikify"`.

**Extra deps** (beyond frappe-ui v1): `splitpanes` (resizable two-column);
`vuedraggable@^4` (drag tree); `pdfjs-dist` (PDF tab) or an `<iframe>` to the File URL
with `#page=N`. **No CodeMirror deps needed** — the markdown editor is frappe-ui's
`CodeEditor` (it lazy-loads CodeMirror 6 + `@codemirror/lang-markdown` itself).

**Stack & setup (as built in Slice 1a):** **frappe-ui `1.0.0-beta.10` + Vite 8 +
Tailwind 3.4** (yarn, matching the bench). The single source of integration truth is
the `frappeui({ frontendRoute: '/wikify' })` vite plugin — it defines
`__FRONTEND_ROUTE__`, wires the dev proxy + jinja boot, builds to
`wikify/public/frontend/`, and **emits the host page to `wikify/www/wikify.html`**
(gitignored, like gameplan's `g.html`). `app.use(FrappeUI)` + one `<FrappeUIProvider>`;
import via the `exports` subpaths (`frappe-ui`, `frappe-ui/code-editor`,
`frappe-ui/vite`). In dev, `main.js` fetches boot from the whitelisted
`get_context_for_dev` and stashes it on `window`; prod gets it inline from
`www/wikify.py`. (This supersedes the earlier Vite-5 / `buildConfig.indexHtmlPath` /
`optimizeDeps.exclude:['frappe-ui']` plan — the `frontendRoute` plugin handles it.)

## Routes

| Path | Page |
|---|---|
| `/wikify` | Imports list |
| `/wikify/import/:name` | Import detail (Tabs) |
| `/wikify/import/:name/:tab` | deep-link a tab (`overview` `pages` `tree` `explore` `wiki`) |
| `/wikify/explore` | Global cross-document explorer |

---

## Phase 1 — Imports list + New Import + Progress

**Imports list** (`pages/ImportList.vue`)
- Fed by `useList("Wikify Import", fields=[name, import_title, status,
  stage_progress, page_count, mean_score, modified], orderBy="modified desc")`.
- Columns: Title · Status (badge) · Progress (inline `<Progress>` when active) ·
  Pages · Score · Updated. Row click → import detail.
- Header button **"New Import"** opens the dialog. Live: subscribe
  `wikify_import_progress` to update the matching row's status/progress in place.

  > **As built (1b):** a custom aligned row list (fixed-width columns + frappe-ui
  > `Badge`/`Progress`), not `ListView` — `ListView`'s per-cell slot exposes only a
  > single cell value, so a status-badge-plus-inline-progress cell can't read the
  > row's other fields. `useList` still drives the data. `ListFilter` deferred.

**New Import dialog** (`components/NewImportDialog.vue`) — frappe-ui `Dialog`
- Step 1 (single step is enough): a FileUploader for the PDF. On select, default the
  **name** input to the filename without extension; the input stays **editable** before
  proceeding (explicit per the brief).
- Action "Start" → `call("wikify.api.imports.start_import", {pdf_file_url, title})` →
  route to the new import's detail. (Wiki Space is *not* asked here — chosen later.)

**Live progress**: in `socket.js` subscribe `wikify_import_progress` →
update a pinia `currentImport` store; the list row and the detail header bind to it.
Stream `wikify_import_log` into the Overview log panel.

**Import detail shell** (`pages/ImportDetail.vue`)
- Header: title, status badge, `<Progress>` while a job runs.
- `Tabs`: Overview · Pages · Tree · Explore · Wiki. Tabs gate on status (Pages
  enabled at `Review`; Tree at `Review`; Explore/Wiki at `Graphed`).
- **Overview tab**: metadata (page count, parser, mean score, model_config),
  per-stage cost (from log meta), and the streaming **log** (virtualized list of
  `Import Log Entry`, colored by level).

---

## Phase 2 — Page Review (split-pane)

`components/PageReview.vue` — `splitpanes` two-column.

**Left pane** — page list: each row = the page **thumbnail** (`Source Page.image`),
page number, a verdict **badge** (`pass`/`escalate`/`review`), a `diagram`/`text`
chip, and a ✓ marker if a remediation was adopted. Filter toggle: All / Flagged
(default to Flagged, as the POC UI did). Backed by `useList("Source Page",
filters={source_document})`.

**Right pane** — selected page, `Tabs`:
1. **PDF** — the original page rendered via PDF.js (or `<iframe src="{pdf}#page=N">`).
   This is the source of truth.
2. **Snapshot** — the stored PNG (`image`): *what the models actually saw* (150 DPI).
   Distinct from tab 1 on purpose — for trust/debugging the parse.
3. **Markdown** — frappe-ui **`CodeEditor`** (`frappe-ui/code-editor`,
   `language="markdown"`, `:disabled="true"` for read-only v1) — the brief's "markdown
   syntax highlighted code editor", now built in. Shows `canonical_markdown`. A
   `TabButtons` toggle **Source ⇄ Preview** swaps to **`CodePreview`** (rendered
   markdown) and **Baseline ⇄ Remediated** reveals the before/after when a remediation
   exists (`baseline_markdown` vs `remediation_markdown`), with score delta + adopted
   flag — the POC's 3-column escalation view, condensed.
4. *(Phase 6)* **Edits** — flip `CodeEditor` to editable (`v-model` →
   `edited_markdown`), diff against canonical, edit indicator.

**Scores strip**: recall / extra / table / judge / composite + verdict; honest metrics
line for visual pages (judge-dominant), as the POC surfaces.

**Actions**: "Remediate flagged" / "Remediate all" → `trigger_remediation`; progress
streams as in Phase 1.

---

## Phase 3 — Tree Review

`components/SectionTree.vue` — **vuedraggable**, adapted from
`apps/wiki/frontend/src/components/NestedDraggable.vue`:
- Recursive self-nesting component; shared `:group="{name:'wikify-tree'}"` to allow
  drag-to-reparent across levels; `.drag-handle` grip.
- Node shape from `Source Section`: `{name, title, is_group, section_type, page_start,
  page_end, include_in_wiki, children}`. Item key = `name`.
- On `@change`: `evt.added` (reparent) or `evt.moved` (reorder) →
  `call("wikify.api.tree.reorder_section", {name, new_parent, new_index, siblings})`
  with the parent's full ordered sibling list (debounce rapid drags, like wiki's
  `moveScheduler`).
- Inline per-node: rename, a **Section Type** picker (Link to `Section Type` with
  colored chips), an include-in-wiki toggle, delete.
- Each node shows its page range; clicking a node can deep-link the Pages tab to that
  range (cross-navigation).
- Header actions: **Reclassify** (`reclassify`) and **Approve & Build Graph**
  (`build_graph`) → advances to `Graphed`, unlocks Explore + Wiki.

(For read-only previews elsewhere — e.g. the Wiki tab projection — use frappe-ui's
display-only `Tree`.)

---

## Phase 4 — Explore

**Per-document Explore tab** (`components/Explore.vue`)
- Section-Type **filter chips** with counts (incl. `other` = "not tagged"), colored by
  `Section Type.color`. "tax / not-tax" filtering from the brief = type vs `other`.
- `ListView` of matching `Source Section` rows: title, hierarchy path, page range,
  type chip; row click opens the section (markdown + jump to its pages).

**Global Explore** (`pages/ExploreGlobal.vue`, `/wikify/explore`)
- The **headline screen**: pick a Section Type → list every matching section **across
  all Source Documents**, grouped by document, with provenance (doc title + page
  range). This is *"give me all the job descriptions across all PDFs"*, answered by a
  metadata filter, not fuzzy search — exactly the POC's design intent.
- Export affordance (copy/markdown/CSV) optional.

---

## Phase 5 — Wiki tab

`components/WikiGenerate.vue`
- **Space selector**: choose an existing Wiki Space (Link/Autocomplete) **or** create
  a new one (name + route) — this is where the deferred space choice happens.
- **Preview**: `preview_wiki` → display the projected Wiki Document tree (frappe-ui
  `Tree`, read-only) so the user sees the page/group structure before committing.
- **Generate** → `generate_wiki`; progress streams; on completion show a link to the
  Wiki Space and per-section "view page" links (`Source Section.wiki_document`).
- Re-generate is idempotent (updates by stored link) — surface "regenerate" once a
  wiki exists.

---

## Cross-cutting UI

- **Auth**: router `beforeEach` guard via a `session` pinia store (copy CRM); www
  `get_context` gates server-side.
- **Realtime**: one `socket.js` with listeners for `wikify_import_progress` and
  `wikify_import_log`, plus frappe-ui's resource auto-refetch on doc updates.
- **Status-driven gating**: tabs/buttons enable per `Wikify Import.status`; a thin
  state badge + stepper in the header communicates where the user is.
- **Empty/error states**: `ListView` `emptyState`; `Failed` imports show the `error`
  and a "Retry" that re-enqueues.
