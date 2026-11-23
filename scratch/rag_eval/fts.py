import sys
from retr import *
from lancedb.index import FTS
install("bge-small-en-v1.5")
table = store.chunks_table()
out = [evaluate("bge-small | fts default", "fts", False), evaluate("bge-small | hybrid default fts", "hybrid", False)]
table.create_index("text", config=FTS(stem=True, remove_stop_words=True), replace=True)
out += [evaluate("bge-small | fts stem+stopwords", "fts", False), evaluate("bge-small | hybrid stem+stopwords", "hybrid", False)]
table.create_index("embed_text", config=FTS(stem=True, remove_stop_words=True), replace=True)
search.FTS_COLUMN = "embed_text"
out += [evaluate("bge-small | fts on embed_text (breadcrumb) stem", "fts", False), evaluate("bge-small | hybrid fts on embed_text stem", "hybrid", False)]
save("fts.json", out)
