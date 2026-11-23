import threading
import numpy as np
from concurrent.futures import ThreadPoolExecutor

def model2vec(name):
    from model2vec import StaticModel
    model = StaticModel.from_pretrained(name)
    def run(texts, is_query=False):
        return [list(map(float, v)) for v in model.encode(texts)]
    return run

def onnx_bert(repo, query_prefix="", pooling="cls", file="onnx/model.onnx", max_len=512):
    import onnxruntime
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer
    tok = Tokenizer.from_file(hf_hub_download(repo, "tokenizer.json"))
    tok.enable_truncation(max_length=max_len)
    tok.enable_padding()
    opts = onnxruntime.SessionOptions(); opts.intra_op_num_threads = 8
    sess = onnxruntime.InferenceSession(hf_hub_download(repo, file), opts)
    names = {i.name for i in sess.get_inputs()}
    def run(texts, is_query=False):
        out = []
        texts = [query_prefix + t for t in texts] if is_query else texts
        for start in range(0, len(texts), 16):
            enc = tok.encode_batch(texts[start:start + 16])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)}
            hidden = sess.run(None, {k: v for k, v in feed.items() if k in names})[0]
            if pooling == "cls":
                vec = hidden[:, 0]
            else:
                vec = (hidden * mask[..., None]).sum(1) / mask.sum(1, keepdims=True)
            vec = vec / np.linalg.norm(vec, axis=1, keepdims=True)
            out.extend(vec.tolist())
        return out
    return run

def openrouter(model, query_prefix=""):
    import requests
    from common import KEY
    def call(batch):
        for attempt in range(4):
            try:
                r = requests.post("https://openrouter.ai/api/v1/embeddings", headers={"Authorization": f"Bearer {KEY['value']}"},
                                  json={"model": model, "input": batch}, timeout=120)
                r.raise_for_status()
                return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]
            except Exception as error:
                last = error
        raise last
    def run(texts, is_query=False):
        texts = [query_prefix + t for t in texts] if is_query else texts
        batches = [texts[i:i + 64] for i in range(0, len(texts), 64)]
        with ThreadPoolExecutor(6) as pool:
            return [v for part in pool.map(call, batches) for v in part]
    return run

EMBEDDERS = {
    "potion-base-8M": lambda: model2vec("minishlab/potion-base-8M"),
    "potion-retrieval-32M": lambda: model2vec("minishlab/potion-retrieval-32M"),
    "bge-small-en-v1.5": lambda: onnx_bert("BAAI/bge-small-en-v1.5", "Represent this sentence for searching relevant passages: "),
    "or:text-embedding-3-small": lambda: openrouter("openai/text-embedding-3-small"),
    "or:qwen3-embedding-8b": lambda: openrouter("qwen/qwen3-embedding-8b", "Instruct: Given a question, retrieve passages that answer it\nQuery: "),
}
