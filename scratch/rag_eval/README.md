# RAG eval harness

Measures retrieval recall and end-to-end answer accuracy of `wikify/rag` on real PDFs.
Findings: `docs/rag-accuracy-eval.md`.

Every run builds its own LanceDB under `$RAG_EVAL_LANCE` (default: the system temp dir).
The site index is never touched.

## Files

| File | What it does |
|---|---|
| `golden.json` | 79 page-grounded questions (gold answer + verbatim evidence) and 10 off-topic questions |
| `gen.py` | Builds `golden.json` from random content pages with Claude Opus 4.5 |
| `embedders.py` | Embedding models under test (model2vec, local ONNX, OpenRouter) |
| `retr.py` | Retrieval recall@k, MRR and latency per embedder and rerank mode |
| `rr.py` | Alternative cross-encoder rerankers (one per process: `RR=L12`, `RR=xsmall`, `RR=bge-reranker`) |
| `fts.py` | FTS tokenizer and breadcrumb (`embed_text`) experiments |
| `sep.py` | Vector score separation between answerable and off-topic questions |
| `e2e.py` | Runs `rag.answer.answer()` per question with timings: `prod`, `improved`, `fast_*` |
| `judge.py` | Grades `e2e` answers against gold with GPT-5.1 |
| `summ.py` | Prints the accuracy, latency and cost matrix from `results/graded_*.json` |
| `results/` | Raw outputs of the runs in the report |

## Run

From the bench root, with `bench start` not required:

```bash
P=apps/wikify/scratch/rag_eval
env/bin/python $P/retr.py potion-base-8M,bge-small-en-v1.5
env/bin/python $P/e2e.py prod
RERANK=0 TOPK=8 MODEL=anthropic/claude-haiku-4.5 env/bin/python $P/e2e.py fast_haiku
env/bin/python $P/judge.py prod fast_haiku
python3 $P/summ.py prod fast_haiku
```

- `WIKIFY_SITE` picks the site (default `wikify.localhost`).
- `common.DOCS` holds the Source Document names of the test PDFs on that site. Change them for another site, then rerun `gen.py`.
- Needs the OpenRouter key in `Wikify Settings`. The account must allow `anthropic/*` and `openai/*` models.
- Run one script at a time on a small machine. Several models in one process can exhaust memory.
