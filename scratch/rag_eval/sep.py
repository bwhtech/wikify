import sys
from retr import *
install("bge-small-en-v1.5")
qs = load("golden.json")
for q in qs:
    hits = search.search(q["question"], project=project_of.get(q["doc"], "PRJ-2026-00004"), limit=5, mode="hybrid", allowed_projects=search.ALL_PROJECTS)
    q["top_vec"] = max((h.vector_score or 0) for h in hits) if hits else 0
pos = sorted(q["top_vec"] for q in qs if q["doc"]); neg = sorted(q["top_vec"] for q in qs if not q["doc"])
print("answerable min/p5/p50", round(pos[0],3), round(pos[len(pos)//20],3), round(pos[len(pos)//2],3))
print("unanswerable max/p50", round(neg[-1],3), round(neg[len(neg)//2],3), [round(x,3) for x in neg])
