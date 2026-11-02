# Transcription guide

How notebook `1_Transcription.ipynb` turns scanned manuscript pages into Markdown transcriptions with Claude.

## Overall workflow

1. **Prepare**: put the scans in `input_images/`, list them in `input_image_manifest.csv`, and check the settings in `pipeline.env`.
2. **Setup cell**: loads the settings and confirms that the API keys are present.
3. **Run options cell**: choose batch or immediate mode, whether to reprocess, and how many pages to do.
4. **Step 1, check**: lists which pages will be processed. It makes no API calls and costs nothing.
5. **Preview (optional)**: shows a page exactly as Claude will receive it.
6. **Step 2, transcribe**: sends each page to Claude together with the transcription rules.
7. **Step 2b, collect** (batch mode only): writes the results once the batch has finished.
8. **Step 3, review**: shows the output manifest and a transcription.

Before sending a page, the pipeline corrects its rotation, shrinks it to at most 2500 px on the longest side, and converts it to JPEG. The original files are never changed.

The transcriptions then feed notebook 2 (building the searchable database) and notebook 3 (asking questions of it).

## API keys

The keys go in `pipeline.env`, in the project folder. Keep this file private and never share or commit it.

```
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=...
```

- **`ANTHROPIC_API_KEY`** is required for transcription. It must be an Anthropic key, which starts with `sk-ant-`. Write it without quotation marks. A key from another provider (for example one starting with `sk-proj-`) gives the error `invalid x-api-key`.
- **`VOYAGE_API_KEY`** is needed only for notebooks 2 and 3, to build and search the database.
- A key left blank in `pipeline.env` falls back to one set in the computer's environment, if any.

After editing `pipeline.env`, re-run the Setup cell. It shows the last four characters of each key so you can check which one is in use.

`pipeline.env` also sets the Claude model (`CLAUDE_MODEL`), how hard it thinks (`CLAUDE_EFFORT`), the image size and quality, and the folder names.

## Input files

Put the scans in **`input_images/`**. Accepted formats are PDF, JPG/JPEG, PNG and TIFF.

- Each page of a PDF or multi-page TIFF is treated as a separate page image.
- One file per page works best. For a multi-page PDF, the output files get `_p01`, `_p02` and so on added to their names.
- Files in the folder that are not listed in the manifest are ignored. Step 1 lists them.

## Input manifest

**`input_image_manifest.csv`** lists the files to process, one per row, in the order they should be done.

| File name | Source |
|---|---|
| GSA_59_66_pages_19_-_28_page_19.pdf | GSA 59/66 |
| GSA_59_66_pages_19_-_28_page_20.pdf | GSA 59/66 |

- **`File name`** is required, and must match the file in `input_images/` exactly, including spaces, underscores and the extension. A name that does not match is skipped, and Step 1 lists it under "Listed but not found".
- **Other columns are optional.** Any column named in the `COPY_MANIFEST_COLUMNS` setting is copied into every transcription's metadata. For example, the setting `{"Source": "collection"}` makes the `Source` column a `collection` field. The current setting is `{}`, which copies nothing.
- If you edit the manifest in Excel, save it as CSV and check that it has not changed the file names.

## Transcription rules

**`transcription_rules.md`** tells Claude how to transcribe. It is sent with every page, so editing it changes all later transcriptions. The current rules are written for nineteenth-century manuscript letters and envelopes:

- **Diplomatic transcription**: original line breaks, spelling, punctuation and abbreviations kept; no translation, correction or modernising.
- **Uncertain readings are marked, never guessed**: `[illegible, c. 3 words]`, `[?Gottschalg?]`, `[?a? / ?b?]`, `[damaged: …]`.
- **Deletions, insertions and underlining** are marked: `~~deleted~~`, `[ins. above: …]`, `<u>underlined</u>`.
- **Marginalia and other hands** (later notes, archive stamps, folio numbers) go in a separate section, with where each one is and what it looks like.
- **Musical notation and figures** are described in brackets where they occur, not transcribed as notes.
- **Metadata** (document type, sender, recipient, place, date) is taken only from what is written on that page.
- **Anything uncertain is flagged**: the file gets `needs_review: true` and a `## For review` section.

A page that cannot be read responsibly is not transcribed. It is recorded in the output manifest as "not usable", with the reason.

## Run settings

These are set in the notebook's **Run options** cell.

| Setting | Values | What it does |
|---|---|---|
| `USE_BATCH` | `True` | Sends all pages together through Anthropic's Batch API at **half price**. Results usually arrive within an hour, at most 24 hours. Run **Step 2b** later to collect them. The notebook can be closed in the meantime. |
| | `False` | Transcribes the pages one at a time while you wait, at full price. Best for testing or a demonstration. |
| `FORCE_REPROCESS` | `False` | Normal setting. Only pages not yet in the output manifest are processed, so you can add new pages and simply re-run. |
| | `True` | Re-transcribes the pages in the manifest even if already done. The earlier files are not deleted: they are moved to `output_transcription_md/_superseded/<date_time>/`. **Set it back to `False` afterwards.** |
| `MAX_IMAGES` | a number, e.g. `3` | Processes only the first that many pages to do. Useful for trying things out. |
| | `None` | Processes every page to do. |
| `COPY_MANIFEST_COLUMNS` | `{}`, or e.g. `{"Source": "collection"}` | Columns of the input manifest to copy into each transcription's metadata, as `{"Column name in CSV": "field_name"}`. `{}` copies none. |

A page that fails, for example because of a network error, is not recorded as done, so it is offered again on the next run.

## Where the outputs go

- **`output_transcription_md/`** has one Markdown file per document item: normally one per page (a letter page, or one side of an envelope). Files are named after the page: `GSA_59_66_pages_19_-_28_page_19_r01.md`. If a page holds several separate items, they are numbered `_r01`, `_r02` and so on. Each file starts with its metadata (source file, document type, sender, recipient, place, date, collection, model, date processed) followed by the transcription.
- **`output_transcr_manifest.csv`** has one row per output file, with its metadata, a word count, and a **Reviewed** column (set to `no`) for keeping track of checking. Pages that were not usable are recorded here too, with the reason under **Notes**.
- **`output_transcription_md/_superseded/`** has earlier versions of reprocessed pages, kept by date and time.
- **`pending_batch.json`** exists only while a batch is waiting to be collected.

To find every transcription that needs checking, look for `revision: true` in the files, or `flagged for review` in the Notes column of the output manifest.
