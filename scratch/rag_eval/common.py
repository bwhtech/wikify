import json, re, os, sys, tempfile, time
from pathlib import Path
import frappe

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
LANCE = Path(os.environ.get("RAG_EVAL_LANCE", Path(tempfile.gettempdir()) / "wikify_rag_eval_lance"))
SITE = os.environ.get("WIKIFY_SITE", "wikify.localhost")
DOCS = {"vrdorea8gk": 40, "84uhv9pk2f": 25, "doeblq8dro": 15}

def norm(text):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()

def connect():
    os.chdir(HERE.parents[3] / "sites")
    frappe.init(site=SITE, sites_path=".")
    frappe.connect()
    openrouter_key()

KEY = {}

def openrouter_key():
    if not KEY:
        from wikify.engine import settings
        KEY["value"] = settings.openrouter_key()
    return KEY["value"]

def chat(model, messages, response_format=None, timeout=180):
    import requests
    body = {"model": model, "messages": messages, "temperature": 0, "usage": {"include": True}}
    if response_format:
        body["response_format"] = response_format
    response = requests.post("https://openrouter.ai/api/v1/chat/completions",
                             headers={"Authorization": f"Bearer {KEY['value']}"}, json=body, timeout=timeout)
    response.raise_for_status()
    return response.json()

def llm_json(model, system, user, label="eval"):
    for attempt in range(3):
        try:
            data = chat(model, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                        response_format={"type": "json_object"})
            text = data["choices"][0]["message"]["content"]
            text = text[text.find("{"): text.rfind("}") + 1]
            return json.loads(text), data.get("usage") or {}
        except Exception as error:
            last = error
            time.sleep(2)
    raise last

def data_path(name):
    return HERE / name if name == "golden.json" else RESULTS / name

def load(name):
    return json.loads(data_path(name).read_text())

def save(name, value):
    RESULTS.mkdir(exist_ok=True)
    data_path(name).write_text(json.dumps(value, indent=1, ensure_ascii=False))
