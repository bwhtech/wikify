import sys, time, json, os
import retr
from common import *
from wikify.rag import answer as rag_answer, search, router
from wikify.engine import settings

config = sys.argv[1]
questions = load("golden.json")
project_of = {**retr.project_of}

if config == "prod":
    retr.install("potion-base-8M")
    search.rerank_scores = retr.section_rerank
elif config == "improved":
    retr.install("bge-small-en-v1.5")
    search.rerank_scores = retr.chunk_rerank
    real_get = settings.get
    settings.get = lambda key, *a, **k: "openai/gpt-4.1-mini" if key == "classifier_model" else real_get(key, *a, **k)

elif config.startswith("fast"):
    retr.install("bge-small-en-v1.5")
    search.rerank_scores = retr.chunk_rerank
    search.RERANK_CANDIDATES = int(os.environ.get("CANDS", "20"))
    search.CANDIDATE_FLOOR = search.RERANK_CANDIDATES
    rag_answer.TOP_K = int(os.environ.get("TOPK", "8"))
    defaults = list(rag_answer.retrieve.__defaults__); defaults[-1] = rag_answer.TOP_K; rag_answer.retrieve.__defaults__ = tuple(defaults)
    model = os.environ.get("MODEL")
    if model:
        from wikify.agent import llm as agent_llm
        rag_answer.llm.resolve_model = lambda *a, **k: model

timings = {}
real_route, real_search = router.route, search.search
def timed(name, fn):
    def wrapper(*a, **k):
        started = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            timings[name] = time.perf_counter() - started
    return wrapper
rag_answer.route = timed("route", real_route)
rag_answer.rag_search.search = timed("retrieve", real_search)

rows = []
for q in questions:
    timings.clear()
    first = {}
    started = time.perf_counter()
    def on_delta(piece):
        first.setdefault("t", time.perf_counter() - started)
    try:
        decided = router.Route("semantic", None, q["question"], "skipped") if config.startswith("fast") else None
        result = rag_answer.answer(q["question"], project=project_of.get(q["doc"], "PRJ-2026-00004"),
                                   allowed_projects=search.ALL_PROJECTS, on_delta=on_delta, decided=decided, rerank=os.environ.get("RERANK", "1") == "1")
    except Exception as error:
        result = {"answer": f"ERROR {error!r}", "citations": [], "refused": False, "route": {}}
    total = time.perf_counter() - started
    rows.append({"id": q["id"], "answer": result["answer"], "refused": result.get("refused"),
                 "route": result.get("route"), "cost": result.get("cost"),
                 "tokens_in": result.get("prompt_tokens") or result.get("input_tokens"),
                 "citations": [{"section": c.get("section"), "page_start": c.get("page_start"), "page_end": c.get("page_end")} for c in result.get("citations", [])],
                 "context_chars": sum(len(c.get("text") or "") for c in result.get("citations", [])),
                 "t_route": round(timings.get("route", 0), 2), "t_retrieve": round(timings.get("retrieve", 0), 2),
                 "t_first_token": round(first.get("t", total), 2), "t_total": round(total, 2)})
    print(q["id"], rows[-1]["t_total"], rows[-1]["refused"], result.get("route", {}).get("intent"), flush=True)
    save(f"e2e_{config}.json", rows)
