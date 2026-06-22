# 0.2 / 03 — Wiki Rendered Preview

Today the Tree tab shows a section's **raw markdown** (and `MarkdownPreview.vue` renders
it prose-styled). But the user can't see **how the page will actually look in the wiki**
until after generation. 0.2 adds an in-app **wiki-fidelity preview**: click a tree node →
see that page rendered as a real wiki page, *before* committing to generation.

## Goal

> "On clicking one of the items, see the preview of how it will look in the actual wiki —
> the rendered preview, as close to wiki as possible."

Two fidelity levers:
1. **Same renderer** the wiki uses (so markdown → HTML matches exactly), not just
   `marked` in the browser.
2. **Same chrome** — a page frame with the title, breadcrumb (from `hierarchy_path`), and
   a sidebar showing the projected page tree, approximating the wiki's reading layout.

## Where it lives

- **Tree tab** (`SectionTree.vue`) — selecting a node opens the preview in the right
  pane (split or panel). This is the primary surface (the user is already curating here).
- **Wiki tab** (`WikiGenerate.vue`) — the projected tree (already shown read-only via
  frappe-ui `Tree`) becomes **clickable**: click a node → same preview. Reinforces "this
  is what you'll get" right before the Generate button.

## Backend — `api/wiki.render_section_preview`

A whitelisted endpoint that renders a single `Source Section` to **the same HTML the wiki
would produce**, so preview ≈ result.

```
render_section_preview(section) -> { title, breadcrumb, html, page_refs_resolved }
```

- **Markdown source:** the section's `markdown` (the same field wiki generation reads).
- **Renderer:** reuse the **wiki app's** markdown→HTML path (the renderer behind
  `Wiki Document` display) so fenced code, tables, and **mermaid** match. If the wiki
  renders client-side, fall back to `frappe.utils.markdown` / `frappe.utils.md_to_html`
  plus the existing `utils/mermaid.js` post-process — the same combination
  `MarkdownPreview.vue` already uses. **Confirm against `apps/wiki` during Slice 15** how
  `Wiki Document.content` is rendered and match it; document the choice in the slice.
- **Breadcrumb:** from `Source Section.hierarchy_path` (already `" > "`-joined ancestors).
- **Page-ref links (preview-only):** run the **pass-2 resolver** logic from
  [`../product/05-wiki-generation.md`](../product/05-wiki-generation.md) in "dry" mode —
  resolve "Page No. N" refs to the target section's *title/anchor* and render them as
  preview links (they can't point at real wiki routes yet, since nothing is generated).
  Count resolved vs. unresolved for a small "N references will become links" hint.

> Rendering on the **backend** (not just reusing the browser `MarkdownPreview`) is what
> buys true fidelity — but if the wiki's display is purely client-side markdown, the
> honest move is to render client-side with the *identical* config and skip the endpoint.
> Decide in Slice 15 after reading `apps/wiki`; the spec allows either, preferring
> whichever the wiki itself uses.

## Frontend — `components/WikiPreview.vue`

A read-only page frame:

```
┌ breadcrumb: Project › Document › Ancestors › Title ────────────┐
│  # Page Title                                                   │
│                                                                 │
│  <rendered wiki HTML — prose, tables, mermaid SVG>              │
│                                                                 │
└────────────────────────────────────────────────────────────────┘
   (optional left rail: the projected page tree, current node active)
```

- Props: `section` (name) or the already-fetched `{title, breadcrumb, html}`.
- Renders `html` via `v-html` inside wiki-matching prose classes (reuse the
  `prose prose-sm` styling from `MarkdownPreview.vue`; add wiki-specific tweaks to close
  the gap). Runs `renderMermaidIn` over the container (same as today).
- Excluded sections (`include_in_wiki = 0`) render with a muted "won't be generated"
  banner.
- A header toggle **Rendered ⇄ Source** swaps to the raw `CodeEditor`/markdown view (so
  the user can still see the source — keeps the 0.1 affordance).

## Reuse, don't rebuild

- `MarkdownPreview.vue` already does marked + mermaid; `WikiPreview.vue` is its
  wiki-framed sibling (breadcrumb + title + tighter wiki styling + page-ref links). Share
  the mermaid util.
- The projected-tree sidebar reuses the data the Wiki tab already builds for its
  read-only `Tree`.

## Acceptance

- Clicking a node in the Tree tab shows it **rendered** (headings, tables, mermaid as
  SVG) inside a wiki-style frame with the correct breadcrumb — visually close to a real
  Wiki Document page.
- A section containing "refer Page No. 130" shows that text as a (preview) link to the
  resolved target section, and the preview reports how many refs will resolve.
- The Wiki tab's projected tree is clickable and opens the same preview.
- Rendered ⇄ Source toggle works; excluded sections are visibly marked.
- The rendered output matches the eventual generated Wiki Document page (spot-check one
  generated page against its preview).
