"""Shared helpers for all three notebooks.

- load_settings(): reads pipeline.env so every stage uses the same models and folders
- ask_claude():    one call to the Claude model named in pipeline.env
- embed_texts():   embeddings from the provider named in pipeline.env
- YAML front matter reading/writing for the Markdown transcriptions
"""

import datetime as dt
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import dotenv_values

PROJECT_DIR = Path(__file__).resolve().parent
ENV_FILE = PROJECT_DIR / "pipeline.env"

# Models that accept Anthropic's server-side refusal fallback (see ask_claude).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    claude_model: str
    claude_effort: str
    embedding_provider: str
    embedding_model: str
    max_image_px: int
    jpeg_quality: int
    pdf_dpi: int
    input_image_dir: Path
    input_manifest: Path
    rules_file: Path
    output_md_dir: Path
    output_manifest: Path
    chroma_dir: Path
    collection_name: str


def load_settings(show=True) -> Settings:
    """Read pipeline.env (re-read every call, so edits take effect without restarting)."""
    if not ENV_FILE.is_file():
        raise FileNotFoundError(
            f"Settings file not found: {ENV_FILE}\n"
            "Copy pipeline.env.example to pipeline.env and fill in your API keys."
        )
    # Only non-blank values are applied, so a blank key in the file does not
    # wipe out a key that is already set in the computer's environment.
    for key, value in dotenv_values(ENV_FILE).items():
        if value not in (None, ""):
            os.environ[key] = value

    env = os.environ.get

    def path(name, default):
        return (PROJECT_DIR / env(name, default)).resolve()

    settings = Settings(
        claude_model=env("CLAUDE_MODEL", "claude-opus-5-5"),
        claude_effort=env("CLAUDE_EFFORT", "high"),
        embedding_provider=env("EMBEDDING_PROVIDER", "voyage").lower(),
        embedding_model=env("EMBEDDING_MODEL", "voyage-3.5"),
        max_image_px=int(env("MAX_IMAGE_PX", "2500")),
        jpeg_quality=int(env("JPEG_QUALITY", "82")),
        pdf_dpi=int(env("PDF_DPI", "200")),
        input_image_dir=path("INPUT_IMAGE_DIR", "input_images"),
        input_manifest=path("INPUT_MANIFEST", "input_image_manifest.csv"),
        rules_file=path("TRANSCRIPTION_RULES", "transcription_rules.md"),
        output_md_dir=path("OUTPUT_MD_DIR", "output_transcription_md"),
        output_manifest=path("OUTPUT_MANIFEST", "output_transcr_manifest.csv"),
        chroma_dir=path("CHROMA_DIR", "chroma-db"),
        collection_name=env("CHROMA_COLLECTION", "transcriptions"),
    )
    if settings.embedding_provider not in ("voyage", "local"):
        raise ValueError('EMBEDDING_PROVIDER must be "voyage" or "local"')
    if show:
        print_settings(settings)
    return settings


def _key_status(name):
    value = os.environ.get(name, "")
    return f"set (...{value[-4:]})" if value else "NOT SET"


def print_settings(settings: Settings):
    print(f"Claude model      : {settings.claude_model}  (effort: {settings.claude_effort})")
    print(f"Embeddings        : {settings.embedding_provider} / {settings.embedding_model}")
    print(f"ANTHROPIC_API_KEY : {_key_status('ANTHROPIC_API_KEY')}")
    if settings.embedding_provider == "voyage":
        print(f"VOYAGE_API_KEY    : {_key_status('VOYAGE_API_KEY')}")
    print(f"Project folder    : {PROJECT_DIR}")


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

_client = None


def claude_client():
    global _client
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise EnvironmentError("ANTHROPIC_API_KEY is not set. Add it to pipeline.env and re-run the Setup cell.")
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def build_request(settings: Settings, system: str, content, json_schema=None, max_tokens=64000) -> dict:
    """The parameters of one Claude request (used both immediately and in batches).

    `content` is a string or a list of content blocks (images, text).
    With `json_schema`, Claude's reply is guaranteed to be JSON matching it.
    The system prompt is cached, so repeating it across many pages is cheap.
    """
    model = settings.claude_model
    request = dict(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
    )
    output_config = {}
    if not model.startswith("claude-haiku"):
        output_config["effort"] = settings.claude_effort
    if json_schema:
        output_config["format"] = {"type": "json_schema", "schema": json_schema}
    if output_config:
        request["output_config"] = output_config
    return request


def message_text(message) -> str:
    """The text of Claude's reply, or an error if the reply is unusable."""
    if message.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request (stop_reason = refusal).")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("Claude's reply was cut off (max_tokens reached).")
    return "".join(block.text for block in message.content if block.type == "text")


def ask_claude(settings: Settings, system: str, content, json_schema=None, max_tokens=64000) -> str:
    """Send one request to Claude now and return the text of its reply."""
    request = build_request(settings, system, content, json_schema, max_tokens)
    client = claude_client()
    if settings.claude_model in FALLBACK_MODELS:
        # If a safety check declines the request, Anthropic re-runs it on a
        # suitable fallback model instead of returning nothing.
        stream = client.beta.messages.stream(
            **request, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
        )
    else:
        stream = client.messages.stream(**request)
    with stream as s:
        return message_text(s.get_final_message())


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

_local_models = {}


def check_embedding_key(settings: Settings):
    if settings.embedding_provider == "voyage" and not os.environ.get("VOYAGE_API_KEY"):
        raise EnvironmentError("VOYAGE_API_KEY is not set. Add it to pipeline.env and re-run the Setup cell.")


def embed_texts(settings: Settings, texts, kind="document"):
    """Return one embedding vector per text. `kind` is "document" or "query"."""
    if not texts:
        return []
    check_embedding_key(settings)
    if settings.embedding_provider == "voyage":
        import voyageai
        client = voyageai.Client()
        vectors = []
        for start in range(0, len(texts), 64):
            result = client.embed(texts[start:start + 64], model=settings.embedding_model, input_type=kind)
            vectors.extend(result.embeddings)
        return vectors

    # Local sentence-transformers model
    from sentence_transformers import SentenceTransformer
    name = settings.embedding_model
    if name not in _local_models:
        _local_models[name] = SentenceTransformer(name)
    if "e5" in name.lower():  # E5 models expect these prefixes
        prefix = "query: " if kind == "query" else "passage: "
        texts = [prefix + t for t in texts]
    return _local_models[name].encode(texts, normalize_embeddings=True).tolist()


# ---------------------------------------------------------------------------
# Markdown files with YAML front matter
# ---------------------------------------------------------------------------

_FRONT_MATTER = re.compile(r"^﻿?---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def read_markdown(path):
    """Return (metadata dict, body text) for a Markdown file with YAML front matter."""
    text = Path(path).read_text(encoding="utf-8")
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}, text
    metadata = yaml.safe_load(match.group(1)) or {}
    for key, value in metadata.items():  # unquoted YAML dates -> "YYYY-MM-DD" strings
        if isinstance(value, (dt.date, dt.datetime)):
            metadata[key] = value.isoformat()
    return metadata, match.group(2)


def _yaml_value(value):
    if value is None or value == "":
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)  # a double-quoted YAML string


def write_markdown(path, metadata: dict, body: str):
    lines = ["---"] + [f"{key}: {_yaml_value(value)}" for key, value in metadata.items()] + ["---", ""]
    Path(path).write_text("\n".join(lines) + body.strip() + "\n", encoding="utf-8")
