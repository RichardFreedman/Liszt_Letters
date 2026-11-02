"""Stage 2: load the Markdown transcriptions into a Chroma vector database.

- Uses the output manifest (output_transcr_manifest.csv) as the list of files to load.
- Copies each file's YAML front matter into the database as filterable metadata.
- Normally only adds files that are new, or that changed since they were loaded.
- Embeddings come from the provider in pipeline.env; the database remembers which
  one was used so the RAG notebook searches with the same one.
"""

import hashlib
import json
import os
import re
from pathlib import Path

os.environ["ANONYMIZED_TELEMETRY"] = "False"  # silence Chroma's telemetry messages

import chromadb
import pandas as pd

from pipeline_common import Settings, check_embedding_key, embed_texts, read_markdown
from transcribe import read_output_manifest

DB_FORMAT_VERSION = "5.0"  # 5.0: manuscript-letter metadata (English field names)
MAX_CHUNK_CHARS = 2400
CHUNK_OVERLAP = 200


# ---------------------------------------------------------------------------
# Opening the database
# ---------------------------------------------------------------------------

def _db_config(settings: Settings):
    return {
        "version": DB_FORMAT_VERSION,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "collection_name": settings.collection_name,
    }


class EmbeddingMismatch(RuntimeError):
    pass


def open_collection(settings: Settings, reset=False, read_only=False):
    """Open (or create) the collection. `reset=True` empties it first.

    Vectors from different embedding models cannot be mixed, so if pipeline.env
    names a different embedding provider/model than the database was built with,
    building empties the collection and starts again, while read-only use
    (previews, searches) stops with an explanation.
    """
    config_path = settings.chroma_dir / "db_config.json"
    wanted = _db_config(settings)
    existing = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else None
    if read_only and existing != wanted:
        raise EmbeddingMismatch(
            f"The database at {settings.chroma_dir} was built with {existing},\n"
            f"but pipeline.env now says {wanted}.\n"
            "Run the Chroma builder notebook (2_Chroma_DB_Builder) to rebuild it with the current settings."
        )
    if existing is not None and existing != wanted and not reset:
        print("Embedding settings differ from the ones this database was built with:")
        print(f"   database : {existing}\n   now      : {wanted}")
        print("The database will be rebuilt from scratch.")
        reset = True

    settings.chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    if reset and settings.collection_name in [c.name for c in client.list_collections()]:
        client.delete_collection(settings.collection_name)
    collection = client.get_or_create_collection(settings.collection_name, metadata={"hnsw:space": "cosine"})
    config_path.write_text(json.dumps(wanted, indent=2), encoding="utf-8")
    return collection


# ---------------------------------------------------------------------------
# Reading and chunking one transcription
# ---------------------------------------------------------------------------

def _date_number(date, upper=False):
    """'1867-03-14' -> 18670314; partial dates fill with 00 (or 99 when upper=True)."""
    match = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", str(date or ""))
    if not match:
        return None
    fill = "99" if upper else "00"
    year, month, day = match.group(1), match.group(2) or fill, match.group(3) or fill
    return int(year + month + day)


def _flat_metadata(front_matter: dict):
    """Chroma metadata may only hold text, numbers and true/false; drop empty values."""
    flat = {}
    for key, value in front_matter.items():
        if value is None or value == "":
            continue
        if isinstance(value, (list, tuple)):
            value = "; ".join(str(v) for v in value)
        elif isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        flat[str(key)] = value
    date_num = _date_number(flat.get("date"))
    if date_num:
        flat["date_num"] = date_num           # used for date-range filters
        flat["year"] = date_num // 10000      # for convenience
    return flat


def _split_text(text):
    """Split long text at paragraph (or sentence/word) boundaries, with a small overlap."""
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = min(start + MAX_CHUNK_CHARS, len(text))
        if end < len(text):
            for sep in ("\n\n", "\n", ". ", " "):
                cut = text.rfind(sep, start + MAX_CHUNK_CHARS // 2, end)
                if cut != -1:
                    end = cut + len(sep)
                    break
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return [c for c in chunks if c]


def parse_transcription(path: Path):
    """Return (metadata, [chunk texts]) for one Markdown transcription.

    The transcription and the marginalia are searchable; the `## For review`
    notes are left out of the searchable text.
    """
    front_matter, body = read_markdown(path)
    metadata = _flat_metadata(front_matter)
    metadata["source_file"] = path.name

    body = re.split(r"^## For review", body, maxsplit=1, flags=re.MULTILINE)[0].strip()

    # A short header on each chunk helps questions like "what did Liszt write to Gottschalg…"
    header = " | ".join(str(metadata[k]) for k in ("title", "date") if k in metadata)
    chunks = [f"{header}\n\n{chunk}" if header else chunk for chunk in _split_text(body)]
    return metadata, chunks


def _file_hash(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Deciding what to load
# ---------------------------------------------------------------------------

def list_transcriptions(settings: Settings):
    """Markdown files listed in the output manifest that exist on disk."""
    names = [row.get("File name", "").strip() for row in read_output_manifest(settings)]
    names = [n for n in dict.fromkeys(names) if n]  # unique, keep order, skip "not usable" rows
    paths = [settings.output_md_dir / n for n in names]
    missing = [p.name for p in paths if not p.is_file()]
    if missing:
        print(f"Listed in the manifest but not found (skipped): {missing}")
    return [p for p in paths if p.is_file()]


def _hashes_in_db(collection):
    stored = collection.get(include=["metadatas"])["metadatas"]
    return {m["source_file"]: m.get("file_hash") for m in stored if m and "source_file" in m}


def preview(settings: Settings):
    """Show what a build would do, and the metadata of each file. Makes no API calls."""
    try:
        in_db = _hashes_in_db(open_collection(settings, read_only=True))
    except EmbeddingMismatch:
        print("The database is empty or was built with other embedding settings: every file will be loaded.\n")
        in_db = {}
    rows = []
    for path in list_transcriptions(settings):
        metadata, chunks = parse_transcription(path)
        stored = in_db.get(path.name)
        status = "new" if stored is None else ("unchanged" if stored == _file_hash(path) else "changed")
        rows.append({"status": status, "chunks": len(chunks), **metadata})
    table = pd.DataFrame(rows)
    if table.empty:
        print("No transcriptions found in the manifest.")
        return table
    counts = table["status"].value_counts().to_dict()
    print(f"Transcriptions in manifest: {len(table)}  ->  {counts}")
    return table


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def build(settings: Settings, force=False):
    """Add new/changed transcriptions to the database. `force=True` rebuilds everything."""
    check_embedding_key(settings)  # fail before touching the database
    collection = open_collection(settings, reset=force)
    in_db = {} if force else _hashes_in_db(collection)
    paths = list_transcriptions(settings)

    added = updated = unchanged = 0
    for path in paths:
        file_hash = _file_hash(path)
        stored = in_db.get(path.name)
        if stored == file_hash:
            unchanged += 1
            continue
        if stored is not None:  # file was edited or re-transcribed: replace its old chunks
            collection.delete(where={"source_file": path.name})

        metadata, chunks = parse_transcription(path)
        metadata["file_hash"] = file_hash
        vectors = embed_texts(settings, chunks, kind="document")
        collection.add(
            ids=[f"{path.name}::{i}" for i in range(len(chunks))],
            documents=chunks,
            embeddings=vectors,
            metadatas=[{**metadata, "chunk": i, "chunks_total": len(chunks)} for i in range(len(chunks))],
        )
        if stored is None:
            added += 1
        else:
            updated += 1
        print(f"  {'updated' if stored else 'added  '} {path.name} ({len(chunks)} chunk(s))")

    # Remove files that are no longer in the manifest (e.g. superseded transcriptions)
    current = {p.name for p in paths}
    removed = sorted(set(in_db) - current)
    for name in removed:
        collection.delete(where={"source_file": name})
        print(f"  removed {name} (no longer in the manifest)")

    print(f"\nDone. Added {added}, updated {updated}, unchanged {unchanged}, removed {len(removed)}.")
    print(f"The database now holds {collection.count()} chunk(s).")


# ---------------------------------------------------------------------------
# Inspecting
# ---------------------------------------------------------------------------

def summary(settings: Settings):
    """Counts and the metadata fields available for filtering in the RAG notebook."""
    collection = open_collection(settings, read_only=True)
    metadatas = collection.get(include=["metadatas"])["metadatas"]
    table = pd.DataFrame(metadatas)
    if table.empty:
        print("The database is empty.")
        return
    print(f"Chunks in database : {len(table)}")
    print(f"Transcriptions     : {table['source_file'].nunique()}")
    for field, label in (("document_type", "Document types"), ("sender", "Senders"), ("recipient", "Recipients")):
        if field in table:
            print(f"{label:<19}: {', '.join(sorted(table[field].dropna().unique()))}")
    if "date" in table:
        dates = table["date"].dropna()
        if not dates.empty:
            print(f"Dates              : {dates.min()} to {dates.max()}")
    print(f"Metadata fields    : {', '.join(sorted(table.columns))}")


def test_search(settings: Settings, question: str, k=3):
    """Quick check that searching works (embeds the question; no Claude call)."""
    collection = open_collection(settings, read_only=True)
    vector = embed_texts(settings, [question], kind="query")
    result = collection.query(query_embeddings=vector, n_results=k)
    for text, meta, distance in zip(result["documents"][0], result["metadatas"][0], result["distances"][0]):
        print(f"--- {meta.get('source_file')} | {meta.get('title')} | {meta.get('date')} | distance {distance:.3f}")
        print(text[:400], "\n")
