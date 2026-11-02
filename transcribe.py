"""Stage 1: transcribe archival page images with Claude.

For every image listed in the input manifest that has not been processed yet:
  1. prepare the page (fix phone-photo rotation, shrink, convert TIFF/PDF to JPEG)
  2. send it to Claude together with transcription_rules.md
  3. write one Markdown file per document item (letter page, envelope side), with YAML front matter
  4. add one row per item to the output manifest

Two ways to send the pages:
  - batch (default): submit_batch() uploads every page at once through Anthropic's
    Batch API at half price; collect_batch() writes the results when they are ready.
  - immediate: transcribe_now() sends one page at a time and waits for each reply.
"""

import base64
import csv
import io
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence

from pipeline_common import (PROJECT_DIR, Settings, ask_claude, build_request, claude_client,
                             message_text, read_markdown, write_markdown)

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".pdf"}
INPUT_FILENAME_COLUMNS = ("File name", "filename", "file_name", "Filename", "File")
MAX_IMAGE_BYTES = 3_700_000  # Claude accepts up to 5 MB per image once base64-encoded

OUTPUT_COLUMNS = [
    "File name", "Source image", "Item no. on page", "Document type", "Sender", "Recipient",
    "Place", "Date (YYYY-MM-DD)", "Date source", "Title", "Language", "Word count (body)",
    "Reviewed", "Model", "Processed at", "Notes",
]

# ---------------------------------------------------------------------------
# What Claude is asked to return
# ---------------------------------------------------------------------------

API_INSTRUCTIONS = """You are transcribing archival manuscript scans through an automated API pipeline.
The project's transcription rules follow below. In this pipeline:

- You receive ONE page image per request, already reduced in size. You cannot request crops,
  other images, or the original file. Transcribe what is visible; mark and flag what is not.
- Do not write the YAML front matter yourself. Put the metadata in the JSON fields; the pipeline
  writes the front matter. Use an empty string for any field the rules say to leave empty.
- `body_markdown` holds everything that follows the front matter, in the body structure the
  rules define: the `#` title, the description line, `## Transcription`, and the
  `## Marginalia and other hands` and `## For review` sections when (and only when) needed.
- Return one entry in `items` per document item, in the reading order the rules define.
- If the page cannot be transcribed responsibly, set `page_usable` to false, give the reason in
  `page_note`, and return an empty `items` list. Otherwise leave `page_note` empty.

<transcription_rules>
{rules}
</transcription_rules>"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "page_usable": {"type": "boolean"},
        "page_note": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "document_type": {"type": "string",
                                      "enum": ["letter", "envelope", "postcard", "note", "other"]},
                    "sender": {"type": "string"},
                    "recipient": {"type": "string"},
                    "place": {"type": "string"},
                    "date": {"type": "string"},
                    "date_source": {"type": "string", "enum": ["written", "postmark", "absent"]},
                    "title": {"type": "string"},
                    "language": {"type": "string"},
                    "needs_review": {"type": "boolean"},
                    "body_markdown": {"type": "string"},
                },
                "required": ["document_type", "sender", "recipient", "place", "date", "date_source",
                             "title", "language", "needs_review", "body_markdown"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["page_usable", "page_note", "items"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Manifests
# ---------------------------------------------------------------------------

def _read_csv(path):
    if not Path(path).is_file():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def read_input_manifest(settings: Settings):
    """Return the image file names listed in the input manifest, in order."""
    rows = _read_csv(settings.input_manifest)
    if not rows:
        raise FileNotFoundError(f"Input manifest missing or empty: {settings.input_manifest}")
    column = next((c for c in INPUT_FILENAME_COLUMNS if c in rows[0]), None)
    if column is None:
        raise ValueError(f"The input manifest needs a file-name column, e.g. 'File name'. Found: {list(rows[0])}")
    return [row for row in rows if (row.get(column) or "").strip()], column


def read_output_manifest(settings: Settings):
    return _read_csv(settings.output_manifest)


def write_output_manifest(settings: Settings, rows):
    columns = list(OUTPUT_COLUMNS)
    for row in rows:  # keep any extra columns someone has added by hand
        columns += [c for c in row if c not in columns]
    with open(settings.output_manifest, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Step 1: decide what to process
# ---------------------------------------------------------------------------

def plan_work(settings: Settings, force=False, limit=None):
    """List the images to transcribe on this run. Makes no API calls."""
    manifest_rows, column = read_input_manifest(settings)
    listed = [row[column].strip() for row in manifest_rows]
    done = {row.get("Source image", "").strip() for row in read_output_manifest(settings)}

    missing = [name for name in listed if not (settings.input_image_dir / name).is_file()]
    unsupported = [name for name in listed if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS]
    in_folder = {p.name for p in settings.input_image_dir.iterdir()
                 if p.suffix.lower() in SUPPORTED_EXTENSIONS} if settings.input_image_dir.is_dir() else set()
    not_in_manifest = sorted(in_folder - set(listed))

    usable = [name for name in listed if name not in missing and name not in unsupported]
    todo = usable if force else [name for name in usable if name not in done]
    if limit:
        todo = todo[:limit]

    print(f"Images listed in manifest   : {len(listed)}")
    print(f"Already transcribed         : {len([n for n in usable if n in done])}")
    print(f"To process on this run      : {len(todo)}" + ("   (FORCE_REPROCESS is on)" if force else ""))
    if missing:
        print(f"\nListed but not found in {settings.input_image_dir.name}/ (skipped): {missing}")
    if unsupported:
        print(f"\nUnsupported file types (skipped): {unsupported}")
    if not_in_manifest:
        print(f"\nIn the image folder but not in the manifest (ignored): {not_in_manifest}")
    if todo:
        print("\nWill process:", ", ".join(todo))
    pending = _load_pending()
    if pending:
        print(f"\nNOTE: a batch submitted on {pending['submitted']} has not been collected yet. "
              "Run Step 2b before submitting new work.")
    return todo


# ---------------------------------------------------------------------------
# Image preparation
# ---------------------------------------------------------------------------

def _to_jpeg(image: Image.Image, settings: Settings) -> bytes:
    image = image.convert("RGB")
    image.thumbnail((settings.max_image_px, settings.max_image_px))  # only ever shrinks
    quality = settings.jpeg_quality
    while True:
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality)
        if buffer.tell() <= MAX_IMAGE_BYTES or quality <= 40:
            return buffer.getvalue()
        quality -= 10


def prepare_pages(image_path: Path, settings: Settings):
    """Return [(page_stem, page_number or None, jpeg_bytes)] for one input file.

    JPG/PNG give one page; multi-page TIFFs and PDFs give one entry per page.
    """
    stem = image_path.stem
    if image_path.suffix.lower() == ".pdf":
        import pymupdf
        with pymupdf.open(image_path) as pdf:
            images = []
            for page in pdf:
                pix = page.get_pixmap(dpi=settings.pdf_dpi)
                images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    else:
        with Image.open(image_path) as source:
            # exif_transpose applies the rotation that phone cameras record in EXIF
            images = [ImageOps.exif_transpose(frame.copy()) for frame in ImageSequence.Iterator(source)]

    if len(images) == 1:
        return [(stem, None, _to_jpeg(images[0], settings))]
    return [(f"{stem}_p{n:02d}", n, _to_jpeg(img, settings)) for n, img in enumerate(images, 1)]


def preview_page(settings: Settings, file_name: str):
    """Show the prepared (reduced) image exactly as Claude will receive it."""
    from IPython.display import Image as IPImage, display
    for stem, page, data in prepare_pages(settings.input_image_dir / file_name, settings):
        with Image.open(io.BytesIO(data)) as img:
            print(f"{stem}: {img.width} x {img.height} px, {len(data) / 1e6:.1f} MB")
        display(IPImage(data=data, width=700))


# ---------------------------------------------------------------------------
# Step 2: transcribe
# ---------------------------------------------------------------------------

def _body_word_count(body: str) -> int:
    """Words in the Transcription section only (not the title, description or marginalia)."""
    body = body.split("## Transcription", 1)[-1]
    body = re.split(r"^## ", body, maxsplit=1, flags=re.M)[0]
    lines = [line for line in body.splitlines() if line.strip()]
    return len(" ".join(lines).split())


def _blank_to_none(value: str):
    value = (value or "").strip()
    return value or None


def _supersede_old_outputs(settings: Settings, image_name: str, manifest_rows, stamp: str):
    """On a forced re-run, move an image's earlier transcripts to _superseded/ (never delete them)."""
    stem = re.escape(Path(image_name).stem)
    pattern = re.compile(rf"^{stem}(_p\d+)?_r\d+\.md$")
    old_files = {row.get("File name", "") for row in manifest_rows if row.get("Source image") == image_name}
    old_files |= {p.name for p in settings.output_md_dir.glob("*.md") if pattern.match(p.name)}
    target = settings.output_md_dir / "_superseded" / stamp
    for name in sorted(n for n in old_files if n):
        path = settings.output_md_dir / name
        if path.is_file():
            target.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), target / name)
    return [row for row in manifest_rows if row.get("Source image") != image_name]


def _page_content(image_name: str, page_number, jpeg: bytes):
    """The user message for one page: the image plus a one-line instruction."""
    return [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                     "data": base64.standard_b64encode(jpeg).decode("ascii")}},
        {"type": "text", "text": f"Transcribe this manuscript page (file: {image_name}"
                                 + (f", page {page_number}" if page_number else "") + ")."},
    ]


def _page_outputs(image_name, page_stem, page_number, result: dict, model: str, extra_yaml: dict):
    """Turn Claude's JSON reply for one page into (files to write, output-manifest rows)."""
    files, rows = [], []
    today = datetime.now().strftime("%Y-%m-%d")
    base_row = {"Source image": image_name, "Model": model, "Processed at": today}

    if not result["page_usable"] or not result["items"]:
        note = result["page_note"] or "no items found"
        print(f"    {page_stem}: not transcribed — {note}")
        rows.append({**base_row, "File name": "", "Notes": f"not usable — {note}"})
        return files, rows

    for n, item in enumerate(result["items"], 1):
        file_name = f"{page_stem}_r{n:02d}.md"
        metadata = {"source_image": image_name}
        if page_number:
            metadata["pdf_page"] = page_number
        metadata.update({
            "item": n,
            "document_type": item["document_type"],
            "sender": _blank_to_none(item["sender"]),
            "recipient": _blank_to_none(item["recipient"]),
            "place": _blank_to_none(item["place"]),
            "date": _blank_to_none(item["date"]),
            "date_source": item["date_source"],
            "title": item["title"],
            "language": item["language"],
            "needs_review": item["needs_review"],
            **extra_yaml,
            "model": model,
            "processed": today,
        })
        files.append((file_name, metadata, item["body_markdown"]))
        rows.append({
            **base_row,
            "File name": file_name,
            "Item no. on page": n,
            "Document type": item["document_type"],
            "Sender": item["sender"],
            "Recipient": item["recipient"],
            "Place": item["place"],
            "Date (YYYY-MM-DD)": item["date"],
            "Date source": item["date_source"],
            "Title": item["title"],
            "Language": item["language"],
            "Word count (body)": _body_word_count(item["body_markdown"]),
            "Reviewed": "no",
            "Notes": "flagged for review" if item["needs_review"] else "",
        })
    print(f"    {page_stem}: {len(result['items'])} item(s)")
    return files, rows


def _save_image_outputs(settings: Settings, image_name, files, rows, output_rows, force, stamp):
    """Write one image's Markdown files and save the manifest (after moving old versions aside if forced)."""
    if force:
        output_rows = _supersede_old_outputs(settings, image_name, output_rows, stamp)
    settings.output_md_dir.mkdir(parents=True, exist_ok=True)
    for file_name, metadata, body in files:
        write_markdown(settings.output_md_dir / file_name, metadata, body)
    output_rows = output_rows + rows
    write_output_manifest(settings, output_rows)
    return output_rows


def _prepare_run(settings: Settings, copy_manifest_columns):
    """The system prompt, and a function giving the extra YAML fields for an image."""
    system_prompt = API_INSTRUCTIONS.format(rules=settings.rules_file.read_text(encoding="utf-8"))
    manifest_rows, column = read_input_manifest(settings)
    input_rows = {row[column].strip(): row for row in manifest_rows}

    def extra_yaml(image_name):
        return {yaml_key: (input_rows[image_name].get(col) or "").strip() or None
                for col, yaml_key in (copy_manifest_columns or {}).items()}
    return system_prompt, extra_yaml


def _print_summary(settings: Settings, total, failures, force, stamp):
    print(f"\nDone. {total - len(failures)} of {total} image(s) transcribed.")
    if failures:
        print(f"Failed (will be retried on the next run): {failures}")
    if force and (settings.output_md_dir / "_superseded" / stamp).exists():
        print(f"Earlier versions were moved to {settings.output_md_dir.name}/_superseded/{stamp}/")


# --- Mode 1: batch (default; half price, results usually within an hour) -----

PENDING_BATCH_FILE = PROJECT_DIR / "pending_batch.json"
MAX_BATCH_BYTES = 250_000_000  # Anthropic's limit is 256 MB per batch


def _load_pending():
    if PENDING_BATCH_FILE.is_file():
        return json.loads(PENDING_BATCH_FILE.read_text(encoding="utf-8"))
    return None


def submit_batch(settings: Settings, todo, force=False, copy_manifest_columns=None):
    """Send all pages to Anthropic's Batch API in one upload and remember the batch ID."""
    pending = _load_pending()
    if pending:
        print(f"A batch submitted on {pending['submitted']} is still waiting to be collected.")
        print("Run Step 2b to collect it (or transcribe.cancel_batch(settings) to cancel it) before submitting another.")
        return
    if not todo:
        print("Nothing to do: every image in the manifest has already been transcribed.")
        return

    system_prompt, extra_yaml = _prepare_run(settings, copy_manifest_columns)
    requests, pages = [], []
    for image_name in todo:
        for page_stem, page_number, jpeg in prepare_pages(settings.input_image_dir / image_name, settings):
            custom_id = f"page-{len(requests):05d}"
            requests.append({"custom_id": custom_id,
                             "params": build_request(settings, system_prompt,
                                                     _page_content(image_name, page_number, jpeg),
                                                     json_schema=RESPONSE_SCHEMA)})
            pages.append({"custom_id": custom_id, "image": image_name,
                          "page_stem": page_stem, "page_number": page_number})
    size = len(json.dumps(requests))
    if size > MAX_BATCH_BYTES:
        raise ValueError(f"This batch is {size / 1e6:.0f} MB, over Anthropic's 256 MB limit. "
                         "Set MAX_IMAGES to split the work into smaller runs.")

    batch = claude_client().messages.batches.create(requests=requests)
    PENDING_BATCH_FILE.write_text(json.dumps({
        "batch_id": batch.id,
        "submitted": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "model": settings.claude_model,
        "force": force,
        "extra_yaml": {image: extra_yaml(image) for image in todo},
        "pages": pages,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Submitted {len(todo)} image(s) ({len(requests)} page(s), {size / 1e6:.1f} MB) as batch {batch.id}.")
    print("Most batches finish within an hour (at most 24 hours). You can close this notebook;")
    print("come back later and run Step 2b to collect the results.")


def collect_batch(settings: Settings):
    """If the submitted batch has finished, write its transcriptions; otherwise report progress."""
    pending = _load_pending()
    if not pending:
        print("No batch is waiting to be collected.")
        return
    client = claude_client()
    batch = client.messages.batches.retrieve(pending["batch_id"])
    counts = batch.request_counts
    if batch.processing_status != "ended":
        print(f"Batch {batch.id} (submitted {pending['submitted']}) is still running: "
              f"{counts.succeeded + counts.errored} of {len(pending['pages'])} page(s) done so far.")
        print("Run this cell again later.")
        return

    results = {r.custom_id: r.result for r in client.messages.batches.results(batch.id)}
    force = pending["force"]
    already_done = {row.get("Source image") for row in read_output_manifest(settings)}
    output_rows = read_output_manifest(settings)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    images = list(dict.fromkeys(page["image"] for page in pending["pages"]))
    failures = []

    for i, image_name in enumerate(images, 1):
        print(f"[{i}/{len(images)}] {image_name}")
        if not force and image_name in already_done:
            print("    already transcribed by another run; batch result not used")
            continue
        files, rows = [], []
        try:
            for page in (p for p in pending["pages"] if p["image"] == image_name):
                result = results.get(page["custom_id"])
                if result is None or result.type != "succeeded":
                    reason = getattr(getattr(result, "error", None), "type", None) or getattr(result, "type", "missing")
                    raise RuntimeError(f"page {page['page_stem']}: {reason}")
                reply = json.loads(message_text(result.message))
                page_files, page_rows = _page_outputs(image_name, page["page_stem"], page["page_number"],
                                                      reply, pending["model"], pending["extra_yaml"][image_name])
                files += page_files
                rows += page_rows
        except Exception as exc:  # the image stays unprocessed and is picked up on the next run
            print(f"    ERROR: {exc}")
            failures.append(image_name)
            continue
        output_rows = _save_image_outputs(settings, image_name, files, rows, output_rows, force, stamp)

    PENDING_BATCH_FILE.unlink()
    _print_summary(settings, len(images), failures, force, stamp)


def cancel_batch(settings: Settings):
    """Cancel the waiting batch (pages already finished are still billed)."""
    pending = _load_pending()
    if not pending:
        print("No batch is waiting.")
        return
    claude_client().messages.batches.cancel(pending["batch_id"])
    PENDING_BATCH_FILE.unlink()
    print(f"Batch {pending['batch_id']} cancelled. Its images will be offered again on the next run.")


# --- Mode 2: immediate (full price; one page at a time, results right away) --

def transcribe_now(settings: Settings, todo, force=False, copy_manifest_columns=None):
    """Transcribe every image in `todo` now. The output manifest is saved after each image,
    so an interruption loses at most the image in progress."""
    if not todo:
        print("Nothing to do: every image in the manifest has already been transcribed.")
        return
    system_prompt, extra_yaml = _prepare_run(settings, copy_manifest_columns)
    output_rows = read_output_manifest(settings)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    failures = []

    for i, image_name in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {image_name}")
        files, rows = [], []
        try:
            for page_stem, page_number, jpeg in prepare_pages(settings.input_image_dir / image_name, settings):
                reply = json.loads(ask_claude(settings, system_prompt, _page_content(image_name, page_number, jpeg),
                                              json_schema=RESPONSE_SCHEMA))
                page_files, page_rows = _page_outputs(image_name, page_stem, page_number, reply,
                                                      settings.claude_model, extra_yaml(image_name))
                files += page_files
                rows += page_rows
        except Exception as exc:  # keep going; the image stays unprocessed and is retried next run
            print(f"    ERROR: {exc}")
            failures.append(image_name)
            continue
        output_rows = _save_image_outputs(settings, image_name, files, rows, output_rows, force, stamp)

    _print_summary(settings, len(todo), failures, force, stamp)


# ---------------------------------------------------------------------------
# Step 3: review
# ---------------------------------------------------------------------------

def show_manifest(settings: Settings, last=20):
    import pandas as pd
    rows = read_output_manifest(settings)
    print(f"{len(rows)} row(s) in {settings.output_manifest.name}")
    return pd.DataFrame(rows).tail(last)


def show_transcription(settings: Settings, file_name: str):
    from IPython.display import Markdown, display
    metadata, body = read_markdown(settings.output_md_dir / file_name)
    for key, value in metadata.items():
        print(f"{key}: {value}")
    # two trailing spaces keep each manuscript line on its own line when rendered
    display(Markdown(body.replace("\n", "  \n")))
