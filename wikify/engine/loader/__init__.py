"""Markdown post-processing — ported from the POC `loader/` package.

Surface: `cleanup` (cross-page boilerplate strip), `cleanup_llm` (cheap text-model
restructure), `table_stitch` (merge tables pymupdf split across a page boundary),
`toc` (embedded-outline heading authority), and `sectionizer` (per-page markdown →
hierarchical `Section`s with page ranges). Logic is unchanged from the POC; the LLM
calls go through `engine.llm` and model ids come from `engine.settings`.
"""
