import json, statistics as st, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
from collections import Counter
g={q["id"]:q for q in json.loads((HERE / 'golden.json').read_text())}
for cfg in sys.argv[1:]:
    r=json.loads((HERE / 'results' / f'graded_{cfg}.json').read_text())
    ans=[x for x in r if x["id"].startswith("Q")]; neg=[x for x in r if x["id"].startswith("N")]
    print("==", cfg, "answerable", dict(Counter(x["grade"] for x in ans)), "unanswerable", dict(Counter(x["grade"] for x in neg)))
    for s in ("keyword","paraphrase","numeric"): print("  ", s, dict(Counter(x["grade"] for x in ans if g[x["id"]]["style"]==s)))
    print("  intents", dict(Counter((x["route"] or {}).get("intent") for x in r)))
    for k in ("t_route","t_retrieve","t_first_token","t_total","context_chars","cost"):
        v=sorted(x[k] or 0 for x in r); print("  ", k, "p50", v[len(v)//2], "p90", v[int(len(v)*.9)], "mean", round(st.mean(v),4))
