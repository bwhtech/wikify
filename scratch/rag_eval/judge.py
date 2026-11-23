import sys, json, re
from concurrent.futures import ThreadPoolExecutor
from common import *
import common
connect()

SYSTEM = """You grade answers from a document question-answering system against a gold answer.
Grades:
- "correct": the answer states the gold answer's key facts (numbers, names, steps) with no contradicting claim.
- "partial": some key facts right, some missing, or vague where the gold is specific.
- "wrong": states a different fact, contradicts the gold, or answers a different question.
- "refused": says the information is not in the wiki / could not find it.
For an UNANSWERABLE question (gold is null): "correct" if the system declines or says it is
not in the documents; "wrong" if it gives a substantive answer as if the documents covered it.
Ignore citation markers and quote formatting. Return JSON {"grade": "...", "why": "one sentence"}."""

def grade(pair):
    q, row = pair
    gold = q["answer"] if q["answer"] else "null (unanswerable: not covered by the documents)"
    user = f"Question: {q['question']}\nGold answer: {gold}\nGold evidence: {q.get('evidence')}\n\nSystem answer:\n{row['answer'][:6000]}"
    out, _ = llm_json("openai/gpt-5.1", SYSTEM, user)
    return {**row, "grade": out.get("grade"), "why": out.get("why")}

questions = {q["id"]: q for q in load("golden.json")}
for config in sys.argv[1:]:
    rows = load(f"e2e_{config}.json")
    with ThreadPoolExecutor(8) as pool:
        graded = list(pool.map(grade, [(questions[r["id"]], r) for r in rows]))
    save(f"graded_{config}.json", graded)
