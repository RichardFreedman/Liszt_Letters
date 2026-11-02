"""Stage 3: ask questions of the Chroma database (retrieval-augmented generation).

1. The question is embedded with the same embedding model the database was built with.
2. The most similar transcription passages are retrieved, optionally filtered by metadata.
3. Claude (the model in pipeline.env) answers using only those passages, citing them.
"""

import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd

from chroma_builder import _date_number, open_collection
from pipeline_common import PROJECT_DIR, Settings, ask_claude, embed_texts

ANSWERS_DIR = PROJECT_DIR / "rag_answers"

DEFAULT_SYSTEM_PROMPT = """You are a research assistant for musicologists working with diplomatic
transcriptions of nineteenth-century manuscript letters and envelopes (mostly German, some French).
Each source is one page or one envelope side; a letter may be spread over several sources.

Answer the question using ONLY the numbered sources provided. Cite every claim with its source
number in square brackets, e.g. [2]. Quote the original when wording matters, and give an English
translation alongside. Keep names, titles and dates exactly as the sources write them.
The transcriptions use editorial markers: [?word?] is an uncertain reading, [illegible] and
[damaged: …] are gaps, ~~text~~ is a deletion, [ins. above: …] an insertion. Say so when an
answer depends on an uncertain reading.
If the sources do not answer the question, say so plainly; do not fill gaps from general knowledge.
Answer in English."""


def open_database(settings: Settings):
    collection = open_collection(settings, read_only=True)
    print(f"Database: {settings.chroma_dir.name}/{settings.collection_name} — {collection.count()} chunk(s)")
    return collection


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

def show_filter_options(collection, fields=("document_type", "sender", "recipient", "place", "year",
                                            "date_source", "language", "needs_review")):
    """List the values present in the database for the most useful metadata fields."""
    metadatas = collection.get(include=["metadatas"])["metadatas"]
    files = {m["source_file"]: m for m in metadatas}  # one entry per transcription
    print(f"{len(files)} transcription(s). All metadata fields: "
          f"{', '.join(sorted({k for m in files.values() for k in m}))}\n")
    for field in fields:
        counts = Counter(m.get(field) for m in files.values() if m.get(field) is not None)
        if counts:
            values = ", ".join(f"{v!r} ({n})" for v, n in sorted(counts.items(), key=lambda kv: str(kv[0])))
            print(f"{field}: {values}")
    dates = sorted(m["date"] for m in files.values() if m.get("date"))
    if dates:
        print(f"date: {dates[0]} to {dates[-1]}")


def build_filter(sender=None, recipient=None, document_type=None, date_from=None, date_to=None,
                 needs_review=None, other=None):
    """Turn the notebook's filter settings into a Chroma `where` filter (None = no filter)."""
    conditions = []
    for field, value in (("sender", sender), ("recipient", recipient), ("document_type", document_type)):
        if value:
            values = [value] if isinstance(value, str) else list(value)
            conditions.append({field: {"$in": values}})
    if date_from:
        conditions.append({"date_num": {"$gte": _date_number(date_from)}})
    if date_to:
        conditions.append({"date_num": {"$lte": _date_number(date_to, upper=True)}})
    if needs_review is not None:
        conditions.append({"needs_review": bool(needs_review)})
    for field, value in (other or {}).items():
        if isinstance(value, (list, tuple)):
            conditions.append({field: {"$in": list(value)}})
        else:
            conditions.append({field: value})
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


# ---------------------------------------------------------------------------
# Retrieval and answer
# ---------------------------------------------------------------------------

def retrieve(settings: Settings, collection, question, k=8, where=None, text_contains=None):
    vector = embed_texts(settings, [question], kind="query")
    query = dict(query_embeddings=vector, n_results=k)
    if where:
        query["where"] = where
    if text_contains:
        query["where_document"] = {"$contains": text_contains}
    result = collection.query(**query)
    return [
        {"text": text, "metadata": meta, "distance": distance}
        for text, meta, distance in zip(result["documents"][0], result["metadatas"][0], result["distances"][0])
    ]


def _source_label(meta):
    parts = [meta.get("title"), meta.get("date"), meta.get("source_file")]
    return " | ".join(str(p) for p in parts if p)


def ask(settings: Settings, collection, question, system_prompt=DEFAULT_SYSTEM_PROMPT,
        k=8, where=None, text_contains=None):
    """Retrieve passages and have Claude answer from them. Returns a result dict."""
    passages = retrieve(settings, collection, question, k, where, text_contains)
    if not passages:
        answer = "No passages matched the question with the current filters."
    else:
        sources = "\n\n".join(
            f'<source id="{i}" label="{_source_label(p["metadata"])}">\n{p["text"]}\n</source>'
            for i, p in enumerate(passages, 1)
        )
        answer = ask_claude(settings, system_prompt,
                            f"<sources>\n{sources}\n</sources>\n\nQuestion: {question}", max_tokens=32000)
    return {"question": question, "answer": answer, "passages": passages, "filter": where,
            "text_contains": text_contains, "model": settings.claude_model,
            "embedding": f"{settings.embedding_provider}/{settings.embedding_model}"}


# ---------------------------------------------------------------------------
# Showing and saving results
# ---------------------------------------------------------------------------

def _result_markdown(result, full_passages=True):
    lines = [f"## Question\n\n{result['question']}", f"## Answer\n\n{result['answer']}", "## Sources"]
    for i, p in enumerate(result["passages"], 1):
        lines.append(f"**[{i}]** {_source_label(p['metadata'])}  (distance {p['distance']:.3f})")
        if full_passages:
            lines.append("> " + p["text"].replace("\n", "\n> "))
    lines.append(f"\n*Model: {result['model']} · Embeddings: {result['embedding']} · "
                 f"Filter: {result['filter']} · Text contains: {result['text_contains']}*")
    return "\n\n".join(lines)


def show_result(result, full_passages=False):
    from IPython.display import Markdown, display
    display(Markdown(_result_markdown(result, full_passages)))


def sources_table(result):
    return pd.DataFrame([{"#": i, "distance": round(p["distance"], 3), **p["metadata"]}
                         for i, p in enumerate(result["passages"], 1)])


def save_result(result, folder=ANSWERS_DIR):
    """Save question, answer and full source passages as a Markdown file."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w]+", "_", result["question"].lower())[:40].strip("_")
    path = folder / f"{datetime.now():%Y%m%d_%H%M%S}_{slug}.md"
    path.write_text(_result_markdown(result, full_passages=True) + "\n", encoding="utf-8")
    print(f"Saved to {path.relative_to(PROJECT_DIR) if path.is_relative_to(PROJECT_DIR) else path}")
    return path
