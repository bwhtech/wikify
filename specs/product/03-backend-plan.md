# 03 — Backend Plan

Phase-by-phase backend work. The throughline: **port the POC pipeline into
`wikify/engine/`, drive it with background jobs, expose thin whitelisted APIs, and
stream progress over realtime.**

## Layout

```
wikify/
  engine/                     # ported pipeline (no Flask, no sqlite)
    parsers/  verify/  loader/
    pdf_utils.py  config.py  llm.py  store.py   # store.py = ORM seam
  api/
    imports.py                # start_import, list helpers
    review.py                 # trigger_remediation, save_page_edit
    tree.py                   # reorder_section, reclassify, build_graph
    wiki.py                   # generate_wiki, preview_wiki
  jobs/
    parse.py  remediate.py  classify.py  generate.py
  wikify/doctype/...          # the DocTypes from 02-data-model.md
  hooks.py                    # website_route_rules, fixtures, roles
```

`engine/store.py` exposes the same call-shapes the POC used against `graph.py`
(`create_document`, `add_page_score`, `add_section`, `add_escalation`,
`sections_by_type`, `canonical_mean`, …) but backed by the Frappe ORM — so the ported
pipeline modules change as little as possible.

## LLM client

Port `config.chat_completion` (the single OpenRouter client that records
cost/latency/tokens per call) into `engine/llm.py`. Key from `Wikify Settings`
(`openrouter_api_key`) or site config. Keep the per-call metrics — surface them as
`Import Log Entry.meta` (cost/latency/model) so the UI can show spend per stage,
reusing the POC's benchmark instrumentation.

---

## Phase 0 — Foundations

1. **Create the DocTypes** in `02-data-model.md` (incl. `Section Type` seed fixtures,
   `Wikify Settings`, roles). `Source Section` and must be `is_tree`.
2. **Port `engine/`** from `scratch/pdf_lab/`:
   - Copy `parsers/`, `verify/`, `loader/`, `pdf_utils.py`, `config.py` verbatim;
     fix imports; route all model calls through `engine/llm.py`.
   - Write `engine/store.py` to replace `loader/graph.py` (ORM-backed).
   - `pdf_utils.render_and_extract` writes each page PNG as a **File** doc attached to
     the `Source Page` (`image`), instead of `storage/pages/<id>/page-XXXX.png`.
3. **Job + realtime helpers**: a small `jobs/_util.py` with `publish_progress(import,
   pct, label)` and `log(import, level, stage, msg, meta)` (creates an
   `Import Log Entry` + `publish_realtime`).
4. **Python deps**: ensure `pymupdf4llm`, `pymupdf`/`fitz`, `openai` (OpenRouter
   client), `markdown`/`mistune`, `python-slugify`. Docling optional/lazy (POC keeps
   it behind a lazy import; Python 3.12 venv as in the POC).

Acceptance: `bench execute` can run `engine.parse_pdf(pdf_path)` end-to-end against a
test site and produce Source Document + Source Page + Source Section rows.

> **As built:** Slice 1b ships the thin spine — `engine.parse_pdf` produces
> Source Document + Source Page (render + baseline `pymupdf4llm` parse, no scoring).
> `store.py`'s 1b surface is `create_document` / `add_page` / `set_page_count`;
> scoring/canonical (`add_page_score`, `canonical_mean`) land in Slice 2 and the
> `Source Section` tree in Slice 4. `Wikify Settings` + `engine/llm.py` arrive with
> Slice 2. Deps `pymupdf` + `pymupdf4llm` are in `pyproject.toml` (the latter now
> also pulls a layout/OCR model — heavier than the POC, same `to_markdown` API).

---

## Phase 1 — Imports list + Parse + Progress

**API** `wikify/api/imports.py`:
- `start_import(pdf_file_url, title)` — create `Wikify Import` (`Draft`→`Queued`),
  snapshot `model_config` from settings, `frappe.enqueue("wikify.jobs.parse.run",
  queue="long", import_name=...)`, return the import name. (The SPA also just inserts
  the doc + a `on_submit`/hook enqueues — either is fine; an explicit API is cleaner
  for the dialog.)

**Job** `wikify/jobs/parse.py::run(import_name)` — ports POC `pipeline.process_document`:
1. `status=Parsing`. Render+extract every page (PNG File + ground-truth text + kind).
   `publish_progress` per page; `page_count` set.
2. ToC level map from the embedded outline (`engine/loader/toc`).
3. Per page: baseline parse → `Source Page.baseline_markdown`; score
   (`verify.harness.score_page`, judge on visual/when enabled) → score fields +
   `verdict`. Log lines with cost.
4. Sectionize (`engine/loader/sectionizer`) over canonical markdown → build the
   `Source Section` tree (heading-validation + numbering + ToC precedence, exactly as
   POC). Set `hierarchy_path`, `level`, `page_start/end`, `parent_source_section`.
5. Classify (Phase 3 may re-run): default-eager classify so Explore preview works —
   `engine/loader/classifier`, `ThreadPoolExecutor(classify_workers)`, write
   `section_type`. (Can be deferred to build_graph; see Phase 3.)
6. `mean_score`; create/link `Source Document`; `Wikify Import.status=Review`; final
   `publish_progress(100)`.

On exception: `status=Failed`, write `error`, log an `error` line.

**Realtime channels**: `wikify_import_progress` `{import, percent, stage_label,
status}`; `wikify_import_log` `{import, level, stage, message, meta}`.

Acceptance: from the list, New Import → progress bar animates, logs stream, lands in
`Review` with pages + an initial section tree.

---

## Phase 2 — Page Review + Remediation

**API** `wikify/api/review.py`:
- `trigger_remediation(import_name, scope="all"|"flagged")` — enqueue
  `wikify.jobs.remediate.run`.
- `save_page_edit(source_page, markdown)` — Phase 6 (writes `edited_markdown`,
  `is_edited`); stub now.

**Job** `wikify/jobs/remediate.py` — ports POC `pipeline.remediate_document`:
- Router per page: `kind==visual` or `text_recall<0.85` → **vlm** (image); else
  **cleanup** (text model). Re-score baseline + candidate with the same judge.
  Adoption: cleanup if `recall >= base.recall - cleanup_recall_tolerance`; vlm if
  `composite > base.composite`. Write `remediation_*`, `remediation_adopted`,
  recompute `canonical_*`.
- After remediation: stitch cross-page tables (`engine/loader/table_stitch`) over
  canonical markdown, then **rebuild the Source Section tree** (same as POC; respect
  the `adopted` flag — POC gotcha: rebuilding from un-adopted escalations reverts
  visual pages to empty baseline).
- `ThreadPoolExecutor(remediation_workers)`; sequential DB writes after the map.
- `status` cycles `Remediating → Review`.

Acceptance: flagged pages drop after remediation; before↔after + adopted flag visible
per page; mermaid-bearing VLM output preserved.

---

## Phase 3 — Tree Review + Graph approval

**API** `wikify/api/tree.py` (mirror the wiki app's `reorder_wiki_documents`
contract):
- `reorder_section(name, new_parent, new_index, siblings)` — set
  `parent_source_section`, batch-update `sort_order` over the JSON sibling list,
  rebuild the NestedSet only if the parent changed; recompute `level`/`hierarchy_path`
  for the moved subtree.
- `rename_section(name, title)`, `set_section_type(name, section_type)`,
  `toggle_include(name, value)`, `delete_section(name)` (with children).
- `reclassify(import_name, only_changed=True)` — enqueue `wikify.jobs.classify.run`
  to (re)label sections after restructuring.
- `build_graph(import_name)` — finalize: ensure all sections classified, recompute
  `hierarchy_path`/`level`, set `Source Document.status=Graphed`,
  `Wikify Import.status=Tree Review`→`Graphed`. (The "graph" already exists as the
  tree; this is the approval gate that unlocks Explore + Wiki.)

Acceptance: drag-reparent/reorder persists and survives reload; approve advances state
and enables Explore/Wiki.

---

## Phase 4 — Explore (typed, cross-document)

**API** (or pure `useList` from the frontend — prefer the ORM directly):
- Per-doc: `useList("Source Section", filters={source_document, section_type?})`.
- **Cross-document headline query**: `useList("Source Section",
  filters={section_type})` across all docs, with `Source Document.title` joined for
  provenance — the *"all job descriptions across all PDFs"* answer. Mirrors POC
  `graph.sections_by_type`.
- `section_type_counts(source_document?)` — chip counts incl. `other`.

No new jobs; this is query + presentation. Add DB indexes on
`Source Section.section_type` and `(source_document, lft)`.

---

## Phase 5 — Wiki generation

**API** `wikify/api/wiki.py`:
- `preview_wiki(import_name)` — return the projected Wiki Document tree (titles,
  is_group, target routes) without writing.
- `generate_wiki(import_name, wiki_space=None, new_space=None)` — enqueue
  `wikify.jobs.generate.run`.

**Job** `wikify/jobs/generate.py` — see `05-wiki-generation.md` for the full mapping.
Summary: resolve/create the Wiki Space, walk the approved `Source Section` tree, create
`Wiki Document` rows (`frappe.new_doc("Wiki Document").insert()`, content=markdown,
`parent_wiki_document` per tree, `is_group` for non-leaf), then a **second pass** that
rewrites internal "page N" references into `[text](<wiki route>)` links (ported from
`engine/loader/wiki` `_PAGEREF_RE` + `slug_for_page`, resolving to the generated Wiki
Document routes). Store `wiki_document` back on each `Source Section`; set
`Source Document.status=Wiki-Generated`, `wiki_space`, `wiki_root_group`;
`Wikify Import.status=Completed`.

Idempotency: re-generation updates existing `Wiki Document`s by stored link (don't
duplicate); deletions/moves reconciled against the current tree.

---

## Cross-cutting

- **Permissions**: `Wikify User` role; owner-scoped Imports. Wiki creation runs
  `ignore_permissions=True` inside the job (server-side), as the wiki app's own
  install/migration code does.
- **hooks.py**: `website_route_rules` → SPA page; `fixtures` for `Section Type`;
  realtime needs no special hook (uses `frappe.publish_realtime`).
- **Failure/retry**: each job is re-enqueueable; `start over` resets status. Keep the
  POC's robustness fixes (judge JSON fence-stripping/retry, VLM fallback at lower DPI).
- **Cost telemetry**: surface per-stage spend (the POC's `get_metrics`) in the
  Overview tab via aggregated `Import Log Entry.meta`.
