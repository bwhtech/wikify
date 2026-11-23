import sys, time, os, json, statistics
from common import *
connect()
from wikify.rag import chunk, search, store, embed, index, rerank
import embedders

PROJECTS = ["PRJ-2026-00004", "PRJ-2026-00003", "PRJ-2026-00002"]
golden = [q for q in load("golden.json") if q["doc"]]
project_of = {d: frappe.db.get_value("Source Document", d, "project") for d in {q["doc"] for q in golden}}
section_md = {r.name: norm(r.markdown) for r in frappe.get_all("Source Section", filters={"source_document": ["in", list(project_of)]}, fields=["name", "markdown"])}

chunks = [c for p in PROJECTS for c in chunk.chunks_for_project(p)]
chunk_text = {c.id: f"{chunk.contextual_prefix('', c.hierarchy_path)}\n\n{c.text}" for c in chunks}
print("chunks", len(chunks))

def correct(q, hit):
    ev = norm(q["evidence"])[:50]
    return ev in section_md.get(hit.section, "")

def install(name):
    run = embedders.EMBEDDERS[name]()
    probe = run(["probe"])[0]
    store.lance_path = lambda: str(LANCE / name.replace(":", "_").replace("/", "_"))
    embed.embed = lambda texts: run(texts, is_query=True)
    import pyarrow as pa
    dim = len(probe)
    original = store.chunk_schema
    def schema():
        s = original()
        return s.set(s.get_field_index("vector"), pa.field("vector", pa.list_(pa.float32(), dim)))
    store.chunk_schema = schema
    os.makedirs(store.lance_path(), exist_ok=True)
    table = store.chunks_table()
    if table is None or table.count_rows() != len(chunks):
        table = store.reset_chunks_table()
        started = time.time()
        vectors = run([c.embed_text for c in chunks])
        table.add([chunk.chunk_row(c, v) for c, v in zip(chunks, vectors)])
        index.refresh_fts_index(table)
        print(name, "index seconds", round(time.time() - started, 1))
    return run

def chunk_rerank(query, hits):
    return dict(enumerate(rerank.scores(query, [chunk_text.get(h.chunk_id, h.text) for h in hits])))

section_rerank = search.rerank_scores

def evaluate(label, mode, use_reranker, rerank_fn=None):
    search.rerank_scores = rerank_fn or section_rerank
    ranks, times = [], []
    for q in golden:
        started = time.perf_counter()
        hits = search.search(q["question"], project=project_of[q["doc"]], limit=8, mode=mode,
                             use_reranker=use_reranker, allowed_projects=search.ALL_PROJECTS)
        times.append(time.perf_counter() - started)
        ranks.append(next((i for i, h in enumerate(hits, 1) if correct(q, h)), None))
    result = {"label": label, "n": len(ranks)}
    for k in (1, 3, 5, 8):
        result[f"R@{k}"] = round(sum(1 for r in ranks if r and r <= k) / len(ranks), 3)
    result["MRR"] = round(sum(1 / r for r in ranks if r) / len(ranks), 3)
    result["p50_ms"] = round(statistics.median(times) * 1000)
    result["p95_ms"] = round(sorted(times)[int(len(times) * .95)] * 1000)
    by_style = {}
    for q, r in zip(golden, ranks):
        by_style.setdefault(q["style"], []).append(bool(r and r <= 5))
    result["R@5_by_style"] = {s: round(sum(v) / len(v), 2) for s, v in by_style.items()}
    result["ranks"] = dict(zip([q["id"] for q in golden], ranks))
    print(json.dumps({k: v for k, v in result.items() if k != "ranks"}), flush=True)
    return result

if __name__ == "__main__":
    results = []
    for name in sys.argv[1].split(","):
        install(name)
        rerank.scores(["warm"][0], ["warm"])
        embed.embed(["warm"])
        if name == "potion-base-8M":
            results.append(evaluate(f"{name} | fts", "fts", False))
        results.append(evaluate(f"{name} | vector", "vector", False))
        results.append(evaluate(f"{name} | hybrid", "hybrid", False))
        results.append(evaluate(f"{name} | hybrid+rerank(section head) [PROD]", "hybrid", True))
        results.append(evaluate(f"{name} | hybrid+rerank(chunk)", "hybrid", True, chunk_rerank))
    existing = load("retrieval.json") if data_path("retrieval.json").exists() else []
    save("retrieval.json", existing + results)
