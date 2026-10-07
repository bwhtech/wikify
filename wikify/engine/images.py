from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

import fitz

from wikify.engine import pdf_utils, regions, store

FIGURE_DPI = 200
CROP_PADDING = 4.0
MERGE_GAP = 2.0
MIN_FIGURE_SIDE = 72.0
MIN_FIGURE_AREA = 0.02
MAX_FIGURE_AREA = 0.85
MIN_REPEATED_PAGES = 3
REPEATED_PAGE_SHARE = 0.2
CAPTION_GAP = 24.0
MAX_CAPTION_CHARS = 120
ANCHOR_LENGTHS = (60, 25)
MIN_ANCHOR_CHARS = 12
EDGE_TOLERANCE = 2.0
MAX_ECHO_CHARS = 120
MIN_TEXT_LAYER_CHARS = 200
_IMAGE_LINE_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
_ECHO_BLOCKER_RE = re.compile(r"^\s*(?:[|<!#>]|```|[-*+]\s|\d+[.)]\s)")
_WORD_RE = re.compile(r"\w{3,}")
_TRANSCRIPTION_START_RE = re.compile(r"^\s*(?:\||<table|```)")

FIGURE_TOKEN_RE = re.compile(r"\[\[FIGURE (\d+)(?::[ \t]*([^\]\n]*))?\]\]")
_CAPTION_RE = re.compile(
	r"^(?:fig(?:ure)?|chart|graph|diagram|photo(?:graph)?|image|illustration|plate|exhibit)\.?\s*[\dA-Za-z]",
	re.IGNORECASE,
)
_SKIPPED_MARKUP_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)|<[^<>]*>|&#?\w+;")
_FENCE_RE = re.compile(r"^[ \t]*```", re.MULTILINE)


@dataclass
class Figure:
	bbox: tuple[float, float, float, float]
	page_size: tuple[float, float] = (1.0, 1.0)
	caption: str = ""
	text_before: str = ""
	text_after: str = ""
	url: str = ""


def repeated_images(pdf_document) -> set[bytes]:
	pages_seen: Counter = Counter()
	for page in pdf_document:
		pages_seen.update({info["digest"] for info in page.get_image_info(hashes=True)})
	threshold = max(MIN_REPEATED_PAGES, pdf_document.page_count * REPEATED_PAGE_SHARE)
	return {digest for digest, count in pages_seen.items() if count >= threshold}


def boxes_touch(box: tuple, other: tuple) -> bool:
	return (
		box[0] - MERGE_GAP <= other[2]
		and other[0] - MERGE_GAP <= box[2]
		and box[1] - MERGE_GAP <= other[3]
		and other[1] - MERGE_GAP <= box[3]
	)


def merge_touching(boxes: list[tuple]) -> list[tuple]:
	merged: list[tuple] = []
	for box in boxes:
		while touching := next((other for other in merged if boxes_touch(box, other)), None):
			merged.remove(touching)
			box = (
				min(box[0], touching[0]),
				min(box[1], touching[1]),
				max(box[2], touching[2]),
				max(box[3], touching[3]),
			)
		merged.append(box)
	return merged


def is_figure_sized(box: tuple, page_box: tuple) -> bool:
	if box[2] - box[0] < MIN_FIGURE_SIDE or box[3] - box[1] < MIN_FIGURE_SIDE:
		return False
	return MIN_FIGURE_AREA <= regions.area_fraction(box, page_box) <= MAX_FIGURE_AREA


def body_text_blocks(page, page_box: tuple) -> list[tuple[tuple, str]]:
	blocks = []
	for block in page.get_text("blocks"):
		text = " ".join((block[4] or "").split())
		box = tuple(float(value) for value in block[:4])
		if block[6] == 0 and text and not regions.is_furniture(box, page_box):
			blocks.append((box, text))
	return blocks


def overlaps_horizontally(box: tuple, other: tuple) -> bool:
	return min(box[2], other[2]) > max(box[0], other[0])


def nearest_block(candidates: list[tuple[tuple, str]], figure_box: tuple, key) -> str:
	aligned = [block for block in candidates if overlaps_horizontally(block[0], figure_box)]
	pool = aligned or candidates
	return min(pool, key=key)[1] if pool else ""


def find_caption(blocks: list[tuple[tuple, str]], figure_box: tuple) -> str:
	for box, text in blocks:
		near_below = 0 <= box[1] - figure_box[3] <= CAPTION_GAP
		near_above = 0 <= figure_box[1] - box[3] <= CAPTION_GAP
		if (
			(near_below or near_above)
			and overlaps_horizontally(box, figure_box)
			and len(text) <= MAX_CAPTION_CHARS
			and _CAPTION_RE.match(text)
		):
			return text
	return ""


def find_figures(page, repeated: set[bytes]) -> list[Figure]:
	page_box = tuple(float(value) for value in page.rect)
	boxes = []
	for info in page.get_image_info(hashes=True):
		if info["digest"] in repeated:
			continue
		box = tuple(float(value) for value in (page.rect & info["bbox"]))
		if regions.area(box) and not regions.is_furniture(box, page_box):
			boxes.append(box)
	figure_boxes = sorted(
		(box for box in merge_touching(boxes) if is_figure_sized(box, page_box)),
		key=lambda box: (box[1], box[0]),
	)

	blocks = body_text_blocks(page, page_box)
	figures = []
	for figure_box in figure_boxes:
		outside = [block for block in blocks if regions.overlap_fraction(block[0], [figure_box]) < 0.5]
		above = [block for block in outside if block[0][3] <= figure_box[1] + EDGE_TOLERANCE]
		below = [block for block in outside if block[0][1] >= figure_box[3] - EDGE_TOLERANCE]
		figures.append(
			Figure(
				bbox=figure_box,
				page_size=(page.rect.width, page.rect.height),
				caption=find_caption(outside, figure_box),
				text_before=nearest_block(above, figure_box, key=lambda block: -block[0][3]),
				text_after=nearest_block(below, figure_box, key=lambda block: block[0][1]),
			)
		)
	return figures


def text_line_boxes(page) -> list[tuple]:
	return [
		tuple(float(value) for value in line["bbox"])
		for block in page.get_text("dict")["blocks"]
		if block["type"] == 0
		for line in block["lines"]
		if "".join(span["text"] for span in line["spans"]).strip()
	]


def figure_clip(page, figure: Figure) -> fitz.Rect:
	"""The padded figure box, moved off any text line its top or bottom edge would cut in half: a line
	mostly inside the picture is taken in whole, a line outside it (a title above) is left out whole."""
	box = figure.bbox
	top, bottom = box[1] - CROP_PADDING, box[3] + CROP_PADDING
	for line in text_line_boxes(page):
		if not overlaps_horizontally(line, box):
			continue
		inside = max(0.0, min(line[3], box[3]) - max(line[1], box[1])) / max(line[3] - line[1], 1.0)
		if line[1] < top < line[3]:
			top = line[1] if inside >= 0.5 else line[3]
		if line[1] < bottom < line[3]:
			bottom = line[3] if inside >= 0.5 else line[1]
	return page.rect & fitz.Rect(box[0] - CROP_PADDING, top, box[2] + CROP_PADDING, bottom)


def crop_figure(page, figure: Figure) -> bytes:
	return pdf_utils.render_png(page, dpi=FIGURE_DPI, clip=figure_clip(page, figure))


def page_figures(page, repeated: set[bytes], page_name: str, page_no: int) -> list[Figure]:
	figures = find_figures(page, repeated)
	for figure in figures:
		figure.url = store.save_crop_file(page_name, page_no, crop_figure(page, figure)).file_url
	return figures


def figure_hint(figures: list[Figure]) -> str:
	if not figures:
		return ""
	lines = []
	for number, figure in enumerate(figures, start=1):
		width, height = figure.page_size
		left, top, right, bottom = figure.bbox
		down = f"{top / height:.0%} to {bottom / height:.0%} down the page"
		across = f"{left / width:.0%} to {right / width:.0%} across"
		lines.append(f"- Figure {number}: {down}, {across}")
	positions = "\n".join(lines)
	return (
		f"\n\nFIGURES: this page has {len(figures)} picture(s) that are cropped from the PDF and shown "
		f"as images, numbered top to bottom:\n{positions}\n"
		"Mark where each picture sits in the reading order with its token on a line of its own, "
		"followed by the picture's printed title when it has one: [[FIGURE 1]] or "
		"[[FIGURE 1: Admissions per year]]. Use every token exactly once and never write Markdown "
		"image tags.\n"
		"- A chart, graph, plot, photo, map or illustration is fully shown by its image: write only "
		"its token. Never turn its bars, lines, axis ticks, legend or data labels into a table, a list "
		"or numbers — the page does not print those values, so anything you write would be invented.\n"
		"- A picture that is mostly text (a flowchart, form, notice, or a table printed as an image): "
		"write its token, then transcribe its text below the token following the rules above.\n"
		"- Text printed outside the pictures is transcribed as usual."
	)


def figure_tag(figure: Figure, number: int, page_no: int, title: str = "") -> str:
	alt = " ".join((figure.caption or title or f"Figure {page_no}.{number}").split())
	return f"![{alt.replace('[', '(').replace(']', ')')}]({figure.url})"


def searchable(markdown: str) -> tuple[str, list[int]]:
	skipped = [match.span() for match in _SKIPPED_MARKUP_RE.finditer(markdown)]
	characters, positions = [], []
	span_index = 0
	for index, character in enumerate(markdown):
		while span_index < len(skipped) and skipped[span_index][1] <= index:
			span_index += 1
		if span_index < len(skipped) and skipped[span_index][0] <= index:
			continue
		if character.isalnum():
			characters.append(character.lower())
			positions.append(index)
	return "".join(characters), positions


def normalized(text: str) -> str:
	return "".join(character.lower() for character in text if character.isalnum())


def layer_text(text: str) -> str:
	return unicodedata.normalize("NFKC", text)


def is_echo(line: str, page_text: str, page_words: set[str]) -> bool:
	"""A short line the parser read off the picture itself: neither the line nor most of its words are
	anywhere in the page's text layer, so it can only be a label or caption drawn inside the image."""
	text = line.strip()
	if not text or len(text) > MAX_ECHO_CHARS or _ECHO_BLOCKER_RE.match(text):
		return False
	if normalized(layer_text(text)) in page_text:
		return False
	words = {word.lower() for word in _WORD_RE.findall(layer_text(text))}
	return len(words & page_words) * 2 < len(words) or not words


def drop_echoes_after_figures(markdown: str, page_text: str) -> str:
	page_words = {word.lower() for word in _WORD_RE.findall(layer_text(page_text))}
	page_text = normalized(layer_text(page_text))
	lines = markdown.splitlines()
	drop: set[int] = set()
	for index, line in enumerate(lines):
		if not _IMAGE_LINE_RE.match(line):
			continue
		echoes = []
		following = index + 1
		while following < len(lines) and (
			not lines[following].strip() or is_echo(lines[following], page_text, page_words)
		):
			if lines[following].strip():
				echoes.append(following)
			following += 1
		if following == len(lines) or not _TRANSCRIPTION_START_RE.match(lines[following]):
			drop.update(echoes)
	if not drop:
		return markdown
	return re.sub(r"\n{3,}", "\n\n", "\n".join(line for index, line in enumerate(lines) if index not in drop))


def drop_figure_echoes(pages: list[tuple[int, str]], pdf_path: str) -> list[tuple[int, str]]:
	"""Drop the labels and captions a parser copied out of a picture into the text after it. Only on pages
	with a text layer to check against, since on a scanned page every line would look drawn."""
	with fitz.open(pdf_path) as pdf_document:
		cleaned = []
		for page_no, markdown in pages:
			page_text = pdf_document[page_no - 1].get_text() if 0 < page_no <= pdf_document.page_count else ""
			if "![" in markdown and len(normalized(layer_text(page_text))) >= MIN_TEXT_LAYER_CHARS:
				markdown = drop_echoes_after_figures(markdown, page_text)
			cleaned.append((page_no, markdown))
	return cleaned


def enclosing_block(markdown: str, index: int) -> tuple[int, int] | None:
	fences = [match.start() for match in _FENCE_RE.finditer(markdown)]
	for opening, closing in zip(fences[::2], fences[1::2], strict=False):
		if opening <= index <= closing:
			line_end = markdown.find("\n", closing)
			return opening, len(markdown) if line_end == -1 else line_end
	table_start = markdown.rfind("<table", 0, index + 1)
	if table_start != -1 and markdown.rfind("</table>", 0, index) < table_start:
		table_end = markdown.find("</table>", index)
		if table_end != -1:
			return table_start, table_end + len("</table>")
	return None


def line_bounds(markdown: str, index: int) -> tuple[int, int]:
	start = markdown.rfind("\n", 0, index) + 1
	end = markdown.find("\n", index)
	end = len(markdown) if end == -1 else end
	block = enclosing_block(markdown, index)
	if block:
		return markdown.rfind("\n", 0, block[0]) + 1, block[1]
	if markdown[start:end].lstrip().startswith("|"):
		while start > 0 and markdown[markdown.rfind("\n", 0, start - 1) + 1 : start].lstrip().startswith("|"):
			start = markdown.rfind("\n", 0, start - 1) + 1
		while end < len(markdown):
			next_end = markdown.find("\n", end + 1)
			next_end = len(markdown) if next_end == -1 else next_end
			if not markdown[end + 1 : next_end].lstrip().startswith("|"):
				break
			end = next_end
	return start, end


def anchor_position(markdown: str, figure: Figure) -> int | None:
	plain, positions = searchable(markdown)
	before, after = normalized(figure.text_before), normalized(figure.text_after)
	for length in ANCHOR_LENGTHS:
		tail, head = before[-length:], after[:length]
		if len(tail) >= MIN_ANCHOR_CHARS and (found := plain.find(tail)) != -1:
			return line_bounds(markdown, positions[found + len(tail) - 1])[1]
		if len(head) >= MIN_ANCHOR_CHARS and (found := plain.find(head)) != -1:
			return line_bounds(markdown, positions[found])[0]
	return None


def insert_at(markdown: str, position: int, tag: str) -> str:
	return f"{markdown[:position].rstrip()}\n\n{tag}\n\n{markdown[position:].lstrip()}"


def place_figures(markdown: str, figures: list[Figure], page_no: int) -> str:
	if not figures:
		return FIGURE_TOKEN_RE.sub("", markdown or "")
	placed: set[int] = set()

	def swap_token(match: re.Match) -> str:
		number = int(match.group(1))
		if not 1 <= number <= len(figures) or number in placed or figures[number - 1].url in markdown:
			return ""
		placed.add(number)
		return figure_tag(figures[number - 1], number, page_no, match.group(2) or "")

	text = FIGURE_TOKEN_RE.sub(swap_token, markdown or "")
	for number, figure in enumerate(figures, start=1):
		if number in placed or not figure.url or figure.url in text:
			continue
		tag = figure_tag(figure, number, page_no)
		position = anchor_position(text, figure)
		if position is None:
			position = 0 if figure.bbox[1] < figure.page_size[1] / 2 else len(text)
		text = insert_at(text, position, tag)
	return re.sub(r"\n{3,}", "\n\n", text).strip()
