import sys, numpy as np
import retr
from retr import *
from wikify.rag import rerank as rr_mod

def onnx_cross(repo, file="onnx/model.onnx", sigmoid=False):
    import onnxruntime
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer
    tok = Tokenizer.from_file(hf_hub_download(repo, "tokenizer.json"))
    tok.enable_truncation(max_length=512, strategy="only_second"); tok.no_padding()
    opts = onnxruntime.SessionOptions(); opts.intra_op_num_threads = 8
    sess = onnxruntime.InferenceSession(hf_hub_download(repo, file), opts)
    names = {i.name for i in sess.get_inputs()}
    def scores(query, texts):
        enc = tok.encode_batch([(query, t) for t in texts]); out = []
        for s in range(0, len(enc), 16):
            b = enc[s:s+16]; w = max(len(e.ids) for e in b)
            pad = lambda rows: np.array([r + [0]*(w-len(r)) for r in rows], dtype=np.int64)
            feed = {"input_ids": pad([e.ids for e in b]), "attention_mask": pad([e.attention_mask for e in b]), "token_type_ids": pad([e.type_ids for e in b])}
            logits = sess.run(None, {k: v for k, v in feed.items() if k in names})[0]
            out.extend(np.reshape(logits, (len(b), -1))[:, -1].tolist())
        return [10 * (1/(1+np.exp(-x))) for x in out]
    return scores

RERANKERS = {
    "ms-marco-MiniLM-L6 (current)": None,
    "ms-marco-MiniLM-L12": lambda: onnx_cross("cross-encoder/ms-marco-MiniLM-L12-v2"),
    "mxbai-rerank-xsmall-v1": lambda: onnx_cross("mixedbread-ai/mxbai-rerank-xsmall-v1"),
    "bge-reranker-base": lambda: onnx_cross("Xenova/bge-reranker-base", "onnx/model_quantized.onnx"),
}
install(sys.argv[1] if len(sys.argv) > 1 else "bge-small-en-v1.5")
out = []
only = os.environ.get('RR')
for name, factory in RERANKERS.items():
    if only and only not in name: continue
    try:
        fn = factory() if factory else rr_mod.scores
    except Exception as error:
        print(name, "LOAD FAILED", repr(error)[:200]); continue
    fn("warm", ["warm"])
    for cands in (50, 20):
        search.RERANK_CANDIDATES = cands
        search.CANDIDATE_FLOOR = cands
        scorer = lambda q, hits, fn=fn: dict(enumerate(fn(q, [chunk_text.get(h.chunk_id, h.text) for h in hits])))
        out.append(evaluate(f"rerank {name} on chunk, {cands} cands", "hybrid", True, scorer))
save(f"rerankers_{only}.json", out)
