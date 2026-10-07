"""Decide which sections become wiki pages; every other section folds into its nearest
page ancestor as an in-page heading. Generation mirrors sections 1:1, so this is where
wiki page granularity is set.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

import frappe

from wikify.engine import llm, settings
from wikify.engine.loader.context import context_block
from wikify.engine.loader.sectionizer import PREAMBLE_TITLE, Section, _section_number
from wikify.engine.store import resolve_parent_indexes

FALLBACK_PAGE_NUMBER_DEPTH = 3
FALLBACK_PAGE_TREE_DEPTH = 1
FRAGMENT_WORDS = 40
LEADING_FRAGMENT_WORDS = 60
MIN_SPLIT_PART_WORDS = 150
MAX_PAGE_WORDS = 2500
MAX_PAGE_PDF_PAGES = 6
OUTLINE_BATCH_LINES = 250

_LIST_ITEM_TITLE = re.compile(r"^(?:\d+[.)]?|[A-Za-z][.)]|[IVXivx]+[.)])\s")

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
	"- Numbered depth-1 sections are always pages; an unnumbered depth-1 fragment (a form label, a "
	"caption, a stray sentence) is not, and merges into the page before it. A section can only be a "
	"page if its parent is one.\n\n"
	'Respond ONLY as JSON: {"pages": [<indices of the sections that start their own wiki page>]}\n\n'
)


def plan_pages(sections: list[Section], project_context: str = "", use_llm: bool = False) -> list[Section]:
	parents = resolve_parent_indexes(sections)
	chosen = llm_pages(sections, parents, project_context) if use_llm else fallback_pages(sections, parents)
	pages, parents = enforce_invariants(sections, parents, chosen)
	return fold_sections(sections, parents, pages)


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


def enforce_invariants(
	sections: list[Section], parents: list[int | None], chosen: set[int]
) -> tuple[set[int], list[int | None]]:
	numbers = outline_numbers(sections, parents)
	child_counts = Counter(parent for parent in parents if parent is not None)
	pages: set[int] = set()
	for index, parent in enumerate(parents):
		if parent is None:
			words = len(sections[index].markdown.split())
			fragment = index not in child_counts and words < FRAGMENT_WORDS
			leading_fragment = (
				index == 0
				and len(sections) > 1
				and index not in child_counts
				and not numbers[index]
				and words < LEADING_FRAGMENT_WORDS
			)
			if not leading_fragment and (index == 0 or numbers[index] or (index in chosen and not fragment)):
				pages.add(index)
		elif index in chosen and parent in pages:
			pages.add(index)
	pages = add_numbered_siblings(parents, numbers, pages)
	pages, parents = split_long_pages(sections, parents, numbers, pages)
	child_counts = Counter(parent for parent in parents if parent is not None)
	pages = {
		index
		for index in pages
		if parents[index] is None
		or index in child_counts
		or (sections[index].markdown.strip() and child_counts[parents[index]] > 1)
	}
	return pages, parents


def add_numbered_siblings(
	parents: list[int | None], numbers: list[tuple[int, ...] | None], pages: set[int]
) -> set[int]:
	"""Numbered siblings are pages alike: once most of them are, the rest follow."""
	numbered_children: dict[int, list[int]] = defaultdict(list)
	for index, parent in enumerate(parents):
		if parent is not None and numbers[index]:
			numbered_children[parent].append(index)
	pages = set(pages)
	for parent in sorted(numbered_children):
		siblings = numbered_children[parent]
		if parent in pages and 2 * len(pages.intersection(siblings)) > len(siblings):
			pages.update(siblings)
	return pages


def split_long_pages(
	sections: list[Section], parents: list[int | None], numbers: list[tuple[int, ...] | None], pages: set[int]
) -> tuple[set[int], list[int | None]]:
	"""A page too long to read in one scroll is split at its numbered children, recursively; tiny
	children stay folded. Without numbered children it splits at its sub-headings instead."""
	parents = list(parents)
	children: dict[int, list[int]] = defaultdict(list)
	subtree_words = [len(section.markdown.split()) for section in sections]
	for index in reversed(range(len(sections))):
		parent = parents[index]
		if parent is not None:
			children[parent].insert(0, index)
			subtree_words[parent] += subtree_words[index]
	pages = set(pages)
	queue = sorted(pages)
	for page in queue:
		folded = [page]
		for index in folded:
			folded.extend(child for child in children[index] if child not in pages)
		words = sum(len(sections[index].markdown.split()) for index in folded)
		pdf_pages = max(sections[index].page_end for index in folded) - sections[page].page_start + 1
		if words <= MAX_PAGE_WORDS and pdf_pages <= MAX_PAGE_PDF_PAGES:
			continue
		split = [
			child
			for child in children[page]
			if numbers[child] and child not in pages and subtree_words[child] >= FRAGMENT_WORDS
		]
		if not split:
			for part in heading_parts(sections, children[page], subtree_words):
				split.append(part[0])
				for member in part[1:]:
					parents[member] = part[0]
					children[part[0]].append(member)
				children[page] = [child for child in children[page] if child not in part[1:]]
		pages.update(split)
		queue.extend(split)
	return pages, parents


def heading_parts(sections: list[Section], children: list[int], subtree_words: list[int]) -> list[list[int]]:
	"""Runs of children that each start at a sub-heading and hold at least MIN_SPLIT_PART_WORDS; a
	tiny run joins the run before it, or the run after it when it is a bare heading."""
	starts = [child for child in children if not _LIST_ITEM_TITLE.match(sections[child].title)]
	if len(starts) < 2:
		starts = children
	parts: list[list[int]] = []
	for child in children:
		if child in starts:
			parts.append([child])
		elif parts:
			parts[-1].append(child)
	merged: list[list[int]] = []
	carried: list[int] = []
	for part in parts:
		part = carried + part
		carried = []
		if sum(subtree_words[child] for child in part) >= MIN_SPLIT_PART_WORDS:
			merged.append(part)
		elif merged and sections[part[0]].markdown.strip():
			merged[-1].extend(part)
		else:
			carried = part
	if carried and merged:
		merged[-1].extend(carried)
	elif carried:
		merged.append(carried)
	return merged if len(merged) > 1 else []


def fold_sections(sections: list[Section], parents: list[int | None], pages: set[int]) -> list[Section]:
	owner: dict[int, int] = {}
	depth_below_page: dict[int, int] = {}
	page_parents = {parents[index] for index in pages}
	leading: list[str] = []
	for index, section in enumerate(sections):
		if index in pages:
			owner[index], depth_below_page[index] = index, 0
			if leading and index not in page_parents:
				section.markdown = "\n\n".join(part for part in (*leading, section.markdown.strip()) if part)
				section.page_start = min(section.page_start, sections[0].page_start)
				leading = []
			continue
		parent = parents[index]
		if parent is None and index - 1 not in owner:
			title = "" if section.title == PREAMBLE_TITLE else f"**{section.title}**"
			leading.extend(part for part in (title, section.markdown.strip()) if part)
			continue
		if parent is None:
			owner[index], depth_below_page[index] = owner[index - 1], 1
		else:
			owner[index], depth_below_page[index] = owner[parent], depth_below_page[parent] + 1
		page = sections[owner[index]]
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
