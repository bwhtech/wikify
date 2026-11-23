# RAG accuracy and speed evaluation

Measured on `wikify.localhost` on 2026-11-20 against the real large PDFs:

| Document | Pages | Sections | Project |
|---|---|---|---|
| Nephrology | 398 | 204 | Neprology (`PRJ-2026-00004`) |
| Obstetrics and Gynaecology | 180 | 96 | Neprology (`PRJ-2026-00004`) |
| EnglishCoreXII | 101 | 28 | Clean Code (`PRJ-2026-00003`) |

Harness, golden set and raw results: `scratch/rag_eval/`.

## Bottom line

| Config | Correct | Partial | Wrong | Off-topic refused | p50 total | p50 first token | Cost per question |
|---|---|---|---|---|---|---|---|
| **Production today** | 53/79 (67%) | 11 | 15 | 10/10 | 4.9 s | 3.3 s | $0.058 |
| Fixed reranker + bge-small + working router | 57/79 (72%) | 18 | 4 | 10/10 | 6.4 s | 4.7 s | $0.068 |
| **Fast: bge-small, no router on first turn, no cross-encoder, Sonnet** | 65/79 (82%) | 8 | 6 | 10/10 | 3.0 s | 1.4 s | $0.076 |
| **Fast, Haiku 4.5** | **65/79 (82%)** | 10 | **4** | **10/10** | **2.6 s** | 1.6 s | **$0.025** |
| Fast, Haiku 4.5, top 5 sections | 63/79 (80%) | 10 | 6 | 10/10 | 2.4 s | 1.6 s | $0.016 |

The fast Haiku config is more accurate, twice as fast, and 57% cheaper than production.

## How we measured

1. **Golden set.** We sampled 80 random content pages (40 Nephrology, 25 Obstetrics, 15 English). Claude Opus 4.5 wrote one question per page in one of three styles: `keyword`, `paraphrase` (no page wording), or `numeric`. Each question carries a gold answer and a verbatim evidence quote. We dropped any question whose quote was not on the page, and any question about headers or document control. 79 remain. We added 10 off-topic questions that the documents do not answer.
2. **Retrieval metric.** A hit is correct when the returned section contains the evidence quote. We report recall@k and MRR.
3. **Answer metric.** We ran `rag.answer.answer()` end to end and graded each answer against gold with GPT-5.1 as the judge: `correct`, `partial`, `wrong` or `refused`.
4. **Isolation.** Every run used a temporary LanceDB under `/tmp`. The site index was not touched.

Caveats:

- The questions come from page text, so they favour keyword search a little. The `paraphrase` slice controls for that.
- The judge is strict. Many `partial` grades mean "right answer plus extra facts from other sections".
- Latency was measured on a dev laptop whose swap was 100% full. CPU numbers are pessimistic.

## What is broken today

### 1. The reranker makes results worse (biggest bug)

`search.rerank_scores` scores `hit.text`. After `expand_to_sections`, `hit.text` is the **whole section markdown**. The cross-encoder truncates to 512 tokens. So it only sees the first ~400 words of each section, and it never sees the passage that matched.

| Retrieval config (potion-base-8M) | R@1 | R@3 | R@8 | p50 |
|---|---|---|---|---|
| hybrid, no rerank | 62% | 82% | 95% | 67 ms |
| **hybrid + rerank on section head (production)** | **57%** | **71%** | **82%** | 1,442 ms |
| hybrid + rerank on the matched chunk | 75% | 91% | 97.5% | 907 ms |

Production pays 1.4 s to lose 13 points of recall@8. Every deep fact in a long section gets pushed out of the top 8. Most of production's 15 wrong answers are false "not in the wiki" replies caused by this bug.

### 2. The router and the section classifier call a model the account cannot use

`classifier_model` is `google/gemini-2.5-flash`. The OpenRouter account enforces zero data retention (ZDR), and every Google endpoint is excluded. Each call returns HTTP 404:

```
0 endpoints out of 1 requested are available matching your guardrail restrictions and data policy ... ZDR violation (account settings)
```

The failure is silent, and it has two effects:

- `rag/router.py` catches the error and falls back to `hybrid`. Every question in the production run routed to `hybrid`.
- `engine/loader/classifier.py` catches the error and returns `"other"`. **Every section of Nephrology and Obstetrics is typed `other`.** So the "list all X" path (metadata filter) cannot work on these documents.

The same ZDR rule also blocks `cleanup_model` (same Gemini model) during ingest.

On this dev site `tabError Log` does not exist, so `frappe.log_error` drops these errors too. Nobody saw them.

### 3. The router costs 1.6 s and changes nothing here

With a working model (`openai/gpt-4.1-mini`) the router returned `semantic` for 88 of 89 questions. It added 1.6 s to every answer. Skipping it on first-turn questions lost no accuracy.

### 4. The refusal gate refuses good questions

`answer.below_floor` refuses when the rerank score is low.

- Production: the gate caught 2 of 10 off-topic questions and wrongly refused 4 answerable ones.
- The answer model refused the other 8 off-topic questions on its own, because of the "use ONLY the excerpts" prompt.
- With the gate off, all 10 off-topic questions were still refused and no answerable one was.
- bge-small vector scores overlap: answerable top scores start at 0.57, off-topic top scores reach 0.61. A score threshold cannot separate them.

### 5. The embedding model is weak

`potion-base-8M` is a static model. Alone it reaches only 48% R@1. Keyword search alone does better (72%).

| Embedder (hybrid, no rerank) | R@1 | R@3 | R@8 | Query p50 | Index 1,020 chunks |
|---|---|---|---|---|---|
| potion-base-8M (today) | 62% | 82% | 95% | 67 ms | <1 s |
| potion-retrieval-32M | 67% | 87% | 97.5% | 51 ms | <1 s |
| **bge-small-en-v1.5 (ONNX, local)** | **75%** | **95%** | **98.7%** | **41 ms** | 78 s |
| OpenAI text-embedding-3-small (API) | 76% | 94% | 97.5% | 918 ms | 10 s |

bge-small runs locally on `onnxruntime` and `tokenizers`, which are already dependencies. It matches the paid API model and adds no network hop.

### 6. Sections go to the LLM whole

Production sends 8 whole sections, about 30k characters (p50) and up to 48k (p90). That is most of the cost and most of the time to first token. The largest section (Nephrology "Research", pages 57 to 73) is 23k characters by itself.

### 7. Smaller items

- `search.search` runs three LanceDB queries (hybrid, vector, FTS) only to get per-list ranks. One vector and one FTS query fused in Python would do the same work.
- `index.upsert_sections` rebuilds the whole FTS index on every section save. That is fine at 1k chunks and slow at 100k.
- Every query logs a LanceDB deprecation warning about `_distance` and `_score` autoprojection.
- `docs/rag-explained.md` still says rerank is "a second, cheap LLM pass". The code now uses a cross-encoder.

## Other experiments

**Stronger rerankers on the matched chunk** (bge-small hybrid underneath):

| Reranker | R@1 | R@3 | R@8 | p50 (20 candidates) |
|---|---|---|---|---|
| none | 75% | 95% | 98.7% | 41 ms |
| ms-marco-MiniLM-L6 (today) | 75% | 94% | 97.5% | 922 ms |
| ms-marco-MiniLM-L12 | 77% | 90% | 100% | 1,808 ms |
| mxbai-rerank-xsmall-v1 | 81% | 92% | 97.5% | 3,513 ms |
| bge-reranker-base (int8) | 82% | 94% | 100% | 2,872 ms |

Better rerankers add 6 to 7 points of R@1 for 2 to 3.5 s on this CPU. They are not worth it while the LLM gets 8 sections. R@8 is already 99%.

**Keyword search over the breadcrumb.** Indexing `embed_text` (title › section path › text) for FTS, not just `text`, raised hybrid R@1 from 75% to **80%** and MRR from 0.85 to 0.88 at no extra latency. That is close to the best reranker for free.

## What still fails in the best config

- **Q75, Q77 (English question bank).** The questions are about exercise numbering ("how many MCQs in the Evans extract"). The sections bundle several exercises, so the model counts the wrong block. This is a sectioning problem, not retrieval.
- **Q18, Q27, Q39.** The manual states different values in different sections (for example "monthly" and "every 3 months"). The model reports both. That is arguably right, and it is a source conflict.
- **Q49, Q67.** The answer is near the gold but less specific.

## Recommended changes, in order

1. **Rerank the matched chunk, not the section head**, or drop the cross-encoder. `search.expand_to_sections` already has the best row. Keep its `text` on the `Hit` and rerank that. *Fixes the biggest accuracy bug.*
2. **Switch the embedder to `BAAI/bge-small-en-v1.5` ONNX** with the query prefix `Represent this sentence for searching relevant passages: `. Reuse the `rerank.py` loading pattern. A full reindex is needed because the dimension changes from 256 to 384. *R@1 62% → 75%.*
3. **Index FTS on `embed_text`.** *R@1 75% → 80%.*
4. **Fix the model config for ZDR.** Set `classifier_model` and `cleanup_model` to a ZDR-allowed model (`anthropic/claude-haiku-4.5` and `openai/gpt-4.1-mini` both work). Then reclassify existing documents. Also stop swallowing the failure: a router or classifier error should show in the UI, not turn into `other`.
5. **Skip the router on first-turn questions.** Run it only when there is chat history, or when the question has a universal word (all, every, list, how many) and the project has more than one section type. *Saves 1.6 s.*
6. **Remove the rerank refusal gate.** Refuse only when retrieval returns nothing. Keep the prompt rule that makes the model say "not in the wiki".
7. **Answer with `anthropic/claude-haiku-4.5`** by default. It matched Sonnet 4.6 on this set at a third of the cost and with a faster finish. Keep Sonnet as a per-project option.
8. **Cap what goes to the LLM.** For sections over about 6k characters, send the matched chunk plus its neighbours, not the whole section. Top 5 cut cost 36% for a 2-question drop. A capped top 8 should keep the accuracy and most of the saving.
9. **Keep this eval.** Move the golden set and harness into `wikify/rag/eval.py` beside the demo-corpus eval, and run it after any retrieval change.

Expected result of 1 to 7: about 82% correct, about 5% wrong, 2.5 s p50, $0.025 per question, against 67%, 19%, 4.9 s and $0.058 today.

## Large PDFs: what the field does

- **Hybrid search plus a structure-aware unit is the norm.** Our section tree already gives contextual chunks for free (`chunk.contextual_prefix`). Anthropic-style contextual retrieval usually costs an LLM call per chunk.
- **Tree or "vectorless" retrieval (PageIndex)** reports 98.7% on FinanceBench. That figure is vendor-reported. An independent 2026 study found no overall winner between hierarchical and vector retrieval (p=0.58). Its edge on long filings came mostly from coverage. A hybrid (vector search to shortlist, then section-tree navigation) is the emerging pattern. We already have the tree, so this is a later option for very long single documents. It is not a first fix.
- **Late chunking** is cheaper than contextual retrieval, but it loses relevance and needs a long-context encoder.
- **Embedders.** For CPU and self-hosted use, the small models in common use are bge-small, nomic-embed v1.5, granite-embedding-small-r2 and EmbeddingGemma. Our test showed bge-small at parity with OpenAI's API model. On OpenRouter, Qwen3-Embedding-8B costs $0.01 per million tokens if an API embedder is ever wanted.
- **Rerankers.** bge-reranker-v2-m3 is the most cited permissive CPU option. Jina v3 is faster but licensed CC-BY-NC. None was worth its CPU cost here.

Sources:

- [Best Open-Source Embedding Models for RAG in 2026 (TigerData)](https://www.tigerdata.com/blog/finding-the-best-open-source-embedding-model-for-rag)
- [Best Self-Hosted Embedding Models in 2026 (Mixpeek)](https://mixpeek.com/curated-lists/best-self-hosted-embedding-models)
- [Granite Embedding R2 Models (arXiv)](https://arxiv.org/pdf/2508.21085)
- [Model2Vec results](https://minish.ai/packages/model2vec/results/)
- [When Does Hierarchical Retrieval Beat Vector Search? (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6476618)
- [VecTree-RAG (arXiv)](https://arxiv.org/pdf/2607.23006)
- [Vectorless RAG: How PageIndex Works](https://www.buildfastwithai.com/blogs/vectorless-rag-pageindex-guide)
- [Reconstructing Context: contextual retrieval vs late chunking (arXiv)](https://arxiv.org/abs/2504.19754)
- [Reranker Benchmark: Top 8 Models Compared (AIMultiple)](https://aimultiple.com/rerankers)
- [Best Rerankers for RAG in 2026 (Mixpeek)](https://mixpeek.com/curated-lists/best-rerankers)
- [Qwen3 Embedding 8B on OpenRouter](https://openrouter.ai/Qwen/Qwen3-Embedding-8B)
- Our own prior art notes: `docs/rag-latency-prior-art.md`
