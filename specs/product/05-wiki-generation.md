# 05 — Wiki Generation (Source Section tree → Wiki Documents)

How an approved `Source Section` tree becomes pages in a Frappe **Wiki Space**. This
is the final phase and the payoff: a navigable, cross-linked wiki.

## Target model (confirmed against `apps/wiki`)

- The live wiki model is **`Wiki Document`** — a NestedSet tree
  (`nsm_parent_field="parent_wiki_document"`). Pages *and* sidebar groups are rows in
  this one doctype. **Legacy `Wiki Page` is hard-deprecated** (its `validate()`
  throws) — do not target it.
- A **`Wiki Space`** owns a `root_group` (a `Wiki Document` with `is_group=1`),
  auto-created on space insert. Every page descends from it; ordering is `sort_order`
  within each parent.
- Create pages the way the wiki app itself does (`install.py`, migrations):
  ```python
  page = frappe.new_doc("Wiki Document")
  page.parent_wiki_document = parent.name
  page.title = "…"
  page.content = "# markdown…"     # content is raw Markdown
  page.is_group = 0                # 1 for a folder/group
  page.is_published = 1            # else 404 + hidden from sidebar
  page.insert(ignore_permissions=True)
  ```
  `validate()` auto-sets `doc_key`, `slug`, `route`, `sort_order`; the `on_update`
  hook keeps `wiki_space` denormalization and the Wiki Revision snapshot in sync —
  **we don't touch the revision system**.
- **Routes** are `<space route>/<ancestor slugs…>/<slug>` and are **globally unique
  for leaf pages** (enforced) — keep titles/slugs distinct within a branch.

## Mapping: structure-preserving (not the POC's L1 collapse)

The POC `loader/wiki.build_wiki` collapsed everything under each L1 ancestor into one
page (because there was no user-arranged tree). **Now we have an approved, editable
`Source Section` tree, so we mirror it 1:1** — a far better result and it uses the
structure the user curated.

For each `Source Section` (depth-first, respecting `sort_order`), where
`include_in_wiki = 1`:

| Source Section | → Wiki Document |
|---|---|
| `is_group = 1` (has children) | `Wiki Document` with `is_group=1` (sidebar folder). Content optional (intro markdown if the section itself has body). |
| leaf (content section) | `Wiki Document` with `is_group=0`, `content = markdown`. |
| `parent_source_section` | → `parent_wiki_document` of the mapped parent. Top-level sections parent to the space `root_group` (or a per-document group — see below). |
| `sort_order` | → `sort_order`. |
| `title` | → `title` (drives slug/route). |

Persist the mapping back: `Source Section.wiki_document = <created name>`.

### One document under a space: per-document group

Since a Wiki Space can hold many Source Documents over time, generate each document's
tree under a **group named after the Source Document** (its own `root_group` child),
stored as `Source Document.wiki_root_group`. This namespaces routes
(`<space>/<doc-slug>/…`) and keeps multiple imports tidy in one space — and supports
the cross-document goal (one space = a corpus you query/browse).

## Pass 2 — page-reference link rewriting

Port `engine/loader/wiki` `_PAGEREF_RE` + `slug_for_page`. The source PDFs cross-
reference by **page number** ("refer Page No. 130", "see page 42"); the wiki has no
page numbers, only links. After all Wiki Documents exist (so targets are resolvable):

1. For each generated page's markdown, find internal page refs (cue word like
   *see/refer* **or** the "Page No. N" form; `N <= page_count`). External book
   citations (e.g. "Williams p820") are left as text.
2. Resolve `N` → the `Source Section` whose `page_start..page_end` contains `N` →ﾠits
   `wiki_document` → that doc's **route**.
3. Replace the reference text with `[original text](/<route>)`.
4. Update the Wiki Document content; count resolved links for the log.

Run pass 2 as a second sweep (update existing docs) so every link target already
exists — the same two-pass ordering the POC used and the reason the memory note
[[wiki-generation-phase-notes]] insisted the tree be built before references resolve.

> Likewise consider figure/section/table cross-refs later — same mechanism, different
> regex + resolver.

## Space selection (the deferred choice)

In the Wiki tab the user either:
- **Selects an existing Wiki Space** → generate under a new per-document group in it; or
- **Creates a new Wiki Space** (name + unique route) → `frappe.new_doc("Wiki Space")`
  (its `root_group` auto-creates), then generate under it.

Store the choice on `Source Document.wiki_space` + `Wikify Import.wiki_space`.

## Idempotency / regeneration

- Track `Source Section.wiki_document`. On regenerate: **update** existing pages by
  that link (title/content/parent/sort_order), **create** newly-included sections,
  and **delete** (`delete_with_children`) sections now excluded or removed from the
  tree. Never blind-duplicate.
- Re-running pass 2 re-resolves links idempotently.
- Because the wiki app maintains its own revision snapshots on `on_update`, each
  regeneration naturally produces a new revision — no extra work.

## Job outline (`wikify/jobs/generate.py`)

```
resolve_or_create_space()
ensure per-document root group → Source Document.wiki_root_group
walk Source Section tree (sorted):           # pass 1 — structure
    upsert Wiki Document (is_group/content/parent/sort_order)
    Source Section.wiki_document = name
    publish_progress
for each generated page:                     # pass 2 — links
    rewrite page-number refs → wiki routes; update content
set Source Document.status = Wiki-Generated; Wikify Import.status = Completed
publish final + space link
```

## Acceptance

- The approved tree appears as a matching Wiki Document sidebar tree under the chosen
  space; pages render markdown (mermaid diagrams included — the wiki renderer handles
  fenced blocks / our content is already mermaid-bearing from VLM remediation).
- Internal "page N" references are clickable links to the right wiki page; external
  citations remain plain text.
- Regeneration after a tree edit updates in place without duplicates.
