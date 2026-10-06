"""Decide which sections become wiki pages; every other section folds into its nearest
page ancestor as an in-page heading. Generation mirrors sections 1:1, so this is where
wiki page granularity is set.
"""

from __future__ import annotations

import json
from collections import Counter

import frappe

from wikify.engine import llm, settings
from wikify.engine.loader.context import context_block
from wikify.engine.loader.sectionizer import Section, _section_number
from wikify.engine.store import resolve_parent_indexes

FALLBACK_PAGE_NUMBER_DEPTH = 3
FALLBACK_PAGE_TREE_DEPTH = 1
OUTLINE_BATCH_LINES = 250

_PROMPT = (
	"You are planning how a PDF document becomes a wiki. Below is its section outline in "
	"document order, one section per line: index | depth | title | PDF pages | own words | "
	"words including subsections | child sections.\n\n"
	"Decide which sections start their own wiki page. Every other section is merged into its "
	"nearest ancestor page as an in-page heading, so no text is lost.\n"
	"- A wiki page is one coherent topic a reader would bookmark, mirroring how the document's "
	"own table of contents presents topics.\n"
	"- Sub-points fold into their parent topic: deeper numbering (e.g. 4.2.1.3 under 4.2.1), "
	"bold or unnumbered sub-headings, and list-like headings (e.g. 'Class I', 'Type 2', "
	"'1. Dosage').\n"
	"- Sections that start on the same PDF page and belong to the same parent topic should "
	"end up on the same wiki page.\n"
	"- Avoid tiny pages (under ~150 words including their subsections) unless the section is "
	"a standalone policy or topic.\n"
	"- Split a very long topic (over ~4000 words) only at its natural numbered sub-sections.\n"
	"- Depth-1 sections are always pages; a section can only be a page if its parent is one.\n\n"
	'Respond ONLY as JSON: {"pages": [<indices of the sections that start their own wiki page>]}\n\n'
)


def plan_pages(sections: list[Section], project_context: str = "", use_llm: bool = False) -> list[Section]:
	parents = resolve_parent_indexes(sections)
	chosen = llm_pages(sections, parents, project_context) if use_llm else fallback_pages(sections, parents)
	return fold_sections(sections, parents, enforce_invariants(sections, parents, chosen))


def tree_depths(parents: list[int | None]) -> list[int]:
	depths: list[int] = []
	for parent in parents:
		depths.append(0 if parent is None else depths[parent] + 1)
	return depths


def outline_numbers(sections: list[Section], parents: list[int | None]) -> list[tuple[int, ...] | None]:
	"""A heading's number only counts when it extends its nearest numbered ancestor's, so a
	list item like "1. Dosage" under 4.2.1 reads as unnumbered."""
	numbers: list[tuple[int, ...] | None] = []
	numbered_ancestor: list[int | None] = []
	for index, section in enumerate(sections):
		parent = parents[index]
		ancestor = None if parent is None else (parent if numbers[parent] else numbered_ancestor[parent])
		number = _section_number(section.title)
		if number and ancestor is not None:
			prefix = numbers[ancestor]
			if len(number) <= len(prefix) or number[: len(prefix)] != prefix:
				number = None
		numbers.append(number)
		numbered_ancestor.append(ancestor)
	return numbers


def fallback_pages(sections: list[Section], parents: list[int | None]) -> set[int]:
	numbers = outline_numbers(sections, parents)
	depths = tree_depths(parents)
	pages: set[int] = set()
	for index in range(len(sections)):
		number = numbers[index]
		has_numbered_ancestor = any(numbers[ancestor] for ancestor in ancestors(parents, index))
		if (
			parents[index] is None
			or (number and len(number) <= FALLBACK_PAGE_NUMBER_DEPTH)
			or (not number and not has_numbered_ancestor and depths[index] <= FALLBACK_PAGE_TREE_DEPTH)
		):
			pages.add(index)
	return pages


def ancestors(parents: list[int | None], index: int):
	parent = parents[index]
	while parent is not None:
		yield parent
		parent = parents[parent]


def enforce_invariants(sections: list[Section], parents: list[int | None], chosen: set[int]) -> set[int]:
	pages: set[int] = set()
	for index, parent in enumerate(parents):
		if parent is None or (index in chosen and parent in pages):
			pages.add(index)
	child_counts = Counter(parent for parent in parents if parent is not None)
	return {
		index
		for index in pages
		if parents[index] is None
		or index in child_counts
		or (sections[index].markdown.strip() and child_counts[parents[index]] > 1)
	}


def fold_sections(sections: list[Section], parents: list[int | None], pages: set[int]) -> list[Section]:
	owner: dict[int, int] = {}
	depth_below_page: dict[int, int] = {}
	for index, section in enumerate(sections):
		if index in pages:
			owner[index], depth_below_page[index] = index, 0
			continue
		parent = parents[index]
		page = sections[owner[parent]]
		owner[index], depth_below_page[index] = owner[parent], depth_below_page[parent] + 1
		heading = f"{'#' * min(6, depth_below_page[index] + 1)} {section.title}"
		page.markdown = "\n\n".join(
			part for part in (page.markdown.strip(), heading, section.markdown.strip()) if part
		)
		page.page_end = max(page.page_end, section.page_end)
	return [section for index, section in enumerate(sections) if index in pages]


def llm_pages(sections: list[Section], parents: list[int | None], project_context: str) -> set[int]:
	chosen: set[int] = set()
	for batch in outline_batches(parents):
		chosen |= ask_for_pages(sections, parents, batch, project_context)
	return chosen


def outline_batches(parents: list[int | None]) -> list[list[int]]:
	chapters: list[list[int]] = []
	for index, parent in enumerate(parents):
		if parent is None:
			chapters.append([])
		chapters[-1].append(index)
	batches: list[list[int]] = []
	for chapter in chapters:
		if batches and len(batches[-1]) + len(chapter) <= OUTLINE_BATCH_LINES:
			batches[-1].extend(chapter)
		else:
			batches.append(list(chapter))
	return batches


def format_outline(sections: list[Section], parents: list[int | None], batch: list[int]) -> str:
	depths = tree_depths(parents)
	own_words = [len(section.markdown.split()) for section in sections]
	total_words = list(own_words)
	child_counts = [0] * len(sections)
	for index in reversed(range(len(sections))):
		parent = parents[index]
		if parent is not None:
			total_words[parent] += total_words[index]
			child_counts[parent] += 1
	lines = []
	for index in batch:
		section = sections[index]
		fields = (
			index,
			depths[index] + 1,
			section.title,
			f"p{section.page_start}-{section.page_end}",
			own_words[index],
			total_words[index],
			child_counts[index],
		)
		lines.append("  " * depths[index] + " | ".join(str(field) for field in fields))
	return "\n".join(lines)


def ask_for_pages(
	sections: list[Section], parents: list[int | None], batch: list[int], project_context: str
) -> set[int]:
	prompt = (
		context_block(project_context) + _PROMPT + "OUTLINE:\n" + format_outline(sections, parents, batch)
	)
	try:
		response = llm.chat_completion(
			settings.get("classifier_model"),
			[{"role": "user", "content": prompt}],
			label="page_plan",
			response_format={"type": "json_object"},
			max_tokens=4096,
			timeout=300,
		)
		pages = json.loads(response["choices"][0]["message"]["content"] or "{}").get("pages")
		if isinstance(pages, str):
			# The Claude CLI backend's open object schema returns the list JSON-encoded.
			pages = json.loads(pages)
	except Exception:
		frappe.log_error(title="Wikify page plan failed")
		pages = None
	batch_indexes = set(batch)
	if not isinstance(pages, list):
		return fallback_pages(sections, parents) & batch_indexes
	return {index for index in pages if type(index) is int and index in batch_indexes}
