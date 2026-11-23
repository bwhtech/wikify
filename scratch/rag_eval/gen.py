import random, re, sys
from concurrent.futures import ThreadPoolExecutor
from common import *
connect()

SYSTEM = """You write evaluation questions for a document question-answering system.
You get ONE page of a document. Write ONE question that a real reader of this document
(clinician, nurse, student, administrator) would plausibly type into a search box, whose
answer is fully contained on this page.

Style for this question: {style}
- "keyword": natural phrasing; may reuse the page's terms.
- "paraphrase": do NOT reuse the distinctive words of the page; describe the need in
  everyday words, as someone who has not seen the page would.
- "numeric": ask for a specific number, dose, time limit, threshold, count or date stated on the page.

Rules: the question must stand alone (no "this page", "the document", "above").
Do not ask about headers, footers, page numbers, authors, document control or tables of contents.
If the page has no usable content (pure index, signatures, blank), return {{"skip": true}}.

Return JSON: {{"question": "...", "answer": "short correct answer", "evidence": "a VERBATIM
quote of 8-30 words copied exactly from the page that proves the answer", "style": "{style}"}}"""

pages = []
import os
LIMIT = int(os.environ.get("LIMIT", "0"))
for doc, count in DOCS.items():
    title = frappe.db.get_value("Source Document", doc, "title")
    rows = frappe.get_all("Source Page", filters={"source_document": doc, "page_no": [">", 10]},
                          fields=["page_no", "canonical_markdown"], order_by="page_no")
    rows = [r for r in rows if len(norm(r.canonical_markdown)) > 700]
    random.Random(7).shuffle(rows)
    for position, row in enumerate(rows[: count + 8]):
        pages.append({"doc": doc, "title": title, "page_no": row.page_no, "text": row.canonical_markdown,
                      "style": ("keyword", "paraphrase", "numeric")[position % 3]})

def make(page):
    try:
        out, _ = llm_json("anthropic/claude-opus-4.5", SYSTEM.format(style=page["style"]),
                          f"Document: {page['title']}\nPage {page['page_no']}:\n\n{page['text'][:9000]}", "eval_gen")
    except Exception as error:
        print('ERR', repr(error)[:300]); return None
    if out.get("skip") or not out.get("question"):
        return None
    if re.search(r"ver\.?\s*:|issue\s*:|pg\.? ?\d+ of|prepared by|approved by|issued by|date\s*:", out.get("evidence", ""), re.I):
        return None
    if norm(out.get("evidence", ""))[:50] not in norm(page["text"]):
        print('NOEVID', out.get('evidence','')[:80]); return None
    return {"doc": page["doc"], "title": page["title"], "page_no": page["page_no"], **out}

pages = pages[:LIMIT] if LIMIT else pages
with ThreadPoolExecutor(8) as pool:
    made = list(pool.map(make, pages))

golden, per_doc = [], {}
for page, item in zip(pages, made):
    if item and per_doc.get(page["doc"], 0) < DOCS[page["doc"]]:
        per_doc[page["doc"]] = per_doc.get(page["doc"], 0) + 1
        golden.append(item)
for position, item in enumerate(golden, 1):
    item["id"] = f"Q{position:02d}"
save("golden.json", golden)
print(len(golden), per_doc)
