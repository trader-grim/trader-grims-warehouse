"""
tgw.apis.google_genai — Gemini Direct API client (google-genai SDK wrapper).

Provides batch pipeline support for full-catalog alt-text sweeps.
The google-genai package is an OPTIONAL dependency — functions that need it
raise ImportError with an install message if absent.

Credentials: secrets_root/google-credentials.json → {"api_key": "..."}
Fallback:    GOOGLE_API_KEY environment variable

Batch flow:
    1. Build task dicts (build_alt_text_task) — pure Python, no SDK needed
    2. Write tasks to JSONL (write_batch_jsonl) — pure Python
    3. submit_batch(tasks, model, cfg, tmpdir) → (job_name, file_name)
    4. poll_batch(job_name, cfg) → terminal state string
    5. download_batch_output(job_name, cfg) → raw JSONL bytes
    6. parse_batch_results(raw_bytes) → list[Optional[list[dict]]]
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_GOOGLE_CRED_FILENAME = "google-credentials.json"

# Per PERPLEXITY-007: 40 images keeps well within token + file-size limits.
# ~5 SKUs × ~8 images = 40; for primary-image-only mode = 40 SKUs/chunk.
BATCH_IMAGES_PER_TASK = 40
BATCH_POLL_INTERVAL_S = 60
BATCH_TIMEOUT_S = 3600 * 4  # 4-hour ceiling

_ALT_TEXT_SYSTEM_PROMPT = (
    "You are an expert in web accessibility and e-commerce SEO. "
    "Respond with valid JSON only — no markdown fences, no commentary."
)


def load_google_key(cfg: Dict[str, Any]) -> str:
    """Return Google API key from secrets_root or GOOGLE_API_KEY env var.

    Raises RuntimeError when the key is absent.
    """
    import os

    cred_path = Path(cfg.get("secrets_root", "/opt/TGW/secrets")) / _GOOGLE_CRED_FILENAME
    if cred_path.exists():
        try:
            data = json.loads(cred_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Cannot read {cred_path}: {exc}") from exc
        key = data.get("api_key") or data.get("key") or data.get("GOOGLE_API_KEY", "")
        if key:
            return key
    env_key = os.environ.get("GOOGLE_API_KEY", "")
    if env_key:
        return env_key
    raise RuntimeError(
        f"Google API key not found in {cred_path} or GOOGLE_API_KEY env var. "
        "Complete todo #153 (Google API key setup) before running this command."
    )


def _require_genai():
    """Import google.genai, raising ImportError with install hint if absent."""
    try:
        import google.genai as genai  # type: ignore[import]
        return genai
    except ImportError as exc:
        raise ImportError(
            "google-genai SDK is required for Gemini Batch API. "
            "Install with: pip install 'google-genai>=0.8'"
        ) from exc


# ---------------------------------------------------------------------------
# Task building — pure Python, no SDK or API key needed
# ---------------------------------------------------------------------------


def build_alt_text_task(
    images_b64: List[str],
    model: str = "gemini-2.5-flash-lite",
) -> Dict[str, Any]:
    """Build one Gemini Batch JSONL task dict for N images.

    The model is instructed to return a JSON array with one object per image
    in submission order: [{"index": 0, "alt_text": "...", "seo_caption": "..."}, ...]

    The caller maintains the (task_index → SKU) mapping.
    """
    n = len(images_b64)
    parts = [
        {"inline_data": {"mime_type": "image/jpeg", "data": b64}}
        for b64 in images_b64
    ]
    parts.append({
        "text": (
            f"Analyze each of the {n} product images above (indexed 0 to {n - 1}). "
            "For each image return an object with: "
            '"alt_text" (concise description ≤150 chars for a screen reader; '
            'do NOT start with "image of" or "picture of"), '
            '"seo_caption" (1-2 sentences mentioning brand, model, and key features '
            "visible in the photo). "
            f"Return a JSON array of exactly {n} objects in index order: "
            '[{"index": 0, "alt_text": "...", "seo_caption": "..."}, ...]'
        )
    })

    return {
        "model": f"models/{model}",
        "contents": [{"role": "user", "parts": parts}],
        "system_instruction": {"parts": [{"text": _ALT_TEXT_SYSTEM_PROMPT}]},
        "generation_config": {
            "response_mime_type": "application/json",
            "temperature": 0.0,
        },
    }


def write_batch_jsonl(tasks: List[Dict[str, Any]], path: Path) -> None:
    """Serialise task dicts to a JSONL file (one JSON object per line)."""
    with path.open("w", encoding="utf-8") as f:
        for task in tasks:
            f.write(json.dumps(task, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Submission and polling — require google-genai SDK + API key
# ---------------------------------------------------------------------------


def submit_batch(
    tasks: List[Dict[str, Any]],
    model: str,
    cfg: Dict[str, Any],
    tmpdir: Path,
) -> Tuple[str, str]:
    """Upload tasks JSONL and create a Gemini Batch job.

    Returns (job_name, input_file_name).  Both are needed to poll and clean up.
    Requires the google-genai SDK and a valid Google API key.
    """
    genai = _require_genai()
    api_key = load_google_key(cfg)
    client = genai.Client(api_key=api_key)

    jsonl_path = tmpdir / "alt-text-batch-input.jsonl"
    write_batch_jsonl(tasks, jsonl_path)

    size_kb = jsonl_path.stat().st_size / 1024
    log.info("Uploading batch JSONL (%d tasks, %.1f KB) …", len(tasks), size_kb)
    uploaded = client.files.upload(file=str(jsonl_path))
    log.info("Uploaded as %s", uploaded.name)

    batch_job = client.batches.create(model=model, src=uploaded)
    log.info("Batch job created: %s (state=%s)", batch_job.name, batch_job.state)
    return batch_job.name, uploaded.name


def poll_batch(
    job_name: str,
    cfg: Dict[str, Any],
    poll_interval_s: int = BATCH_POLL_INTERVAL_S,
    timeout_s: int = BATCH_TIMEOUT_S,
) -> str:
    """Poll batch until a terminal state is reached.  Returns final state string.

    Terminal states contain COMPLETED, FAILED, or CANCELLED.
    Raises TimeoutError if timeout_s elapses.
    """
    genai = _require_genai()
    api_key = load_google_key(cfg)
    client = genai.Client(api_key=api_key)

    deadline = time.time() + timeout_s
    while True:
        job = client.batches.get(name=job_name)
        state = str(job.state)
        log.info("Batch %s → %s", job_name, state)
        if any(s in state for s in ("COMPLETED", "FAILED", "CANCELLED")):
            return state
        if time.time() >= deadline:
            raise TimeoutError(
                f"Batch {job_name} did not reach a terminal state within {timeout_s}s "
                f"(last state: {state})"
            )
        time.sleep(poll_interval_s)


def download_batch_output(job_name: str, cfg: Dict[str, Any]) -> bytes:
    """Download the batch output JSONL bytes from a COMPLETED job.

    Tries client.files.download first; falls back to URI + HTTP when the SDK
    version doesn't have that method.
    """
    genai = _require_genai()
    api_key = load_google_key(cfg)
    client = genai.Client(api_key=api_key)

    job = client.batches.get(name=job_name)
    output_ref = getattr(job, "output_file", None) or getattr(job, "dest", None)
    if not output_ref:
        raise RuntimeError(
            f"Batch job {job_name} has no output_file attribute "
            f"(state={getattr(job, 'state', '?')})"
        )

    # Try SDK download helper first (sdk >= 0.8)
    if hasattr(client.files, "download"):
        result = client.files.download(name=output_ref)
        if isinstance(result, (bytes, bytearray)):
            return bytes(result)

    # Fallback: resolve URI via files.get and download with requests
    import requests as req

    file_meta = client.files.get(name=output_ref)
    uri = getattr(file_meta, "uri", None)
    if not uri:
        raise RuntimeError(f"Cannot resolve download URI for output file {output_ref!r}")
    resp = req.get(uri, headers={"Authorization": f"Bearer {api_key}"}, timeout=120)
    resp.raise_for_status()
    return resp.content


# ---------------------------------------------------------------------------
# Result parsing — pure Python, no SDK needed
# ---------------------------------------------------------------------------


def parse_batch_results(
    raw_bytes: bytes,
) -> List[Optional[List[Dict[str, Any]]]]:
    """Parse a batch output JSONL into per-task result lists.

    Each output line corresponds to one submitted task in order.
    Returns a list of the same length as the input tasks:
      - list[dict] with {index, alt_text, seo_caption} on success
      - None on error / unparseable line
    """
    parsed: List[Optional[List[Dict[str, Any]]]] = []

    for i, line in enumerate(raw_bytes.decode("utf-8", errors="replace").splitlines()):
        line = line.strip()
        if not line:
            continue

        try:
            envelope = json.loads(line)
        except json.JSONDecodeError as exc:
            log.warning("Result line %d: JSON decode error: %s", i, exc)
            parsed.append(None)
            continue

        if "error" in envelope and envelope.get("error"):
            log.warning("Result line %d: API error: %s", i, envelope["error"])
            parsed.append(None)
            continue

        try:
            resp = envelope.get("response") or envelope
            text = resp["candidates"][0]["content"]["parts"][0]["text"]
            items = json.loads(text)
            if not isinstance(items, list):
                # Unwrap single object or items-keyed dict
                if isinstance(items, dict) and "items" in items:
                    items = items["items"]
                else:
                    items = [items]
            parsed.append(items)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            log.warning("Result line %d: parse error: %s", i, exc)
            parsed.append(None)

    return parsed


def cleanup_input_file(input_file_name: str, cfg: Dict[str, Any]) -> None:
    """Delete the uploaded batch input file from Google's servers (best-effort)."""
    try:
        genai = _require_genai()
        api_key = load_google_key(cfg)
        client = genai.Client(api_key=api_key)
        client.files.delete(name=input_file_name)
        log.info("Deleted batch input file %s", input_file_name)
    except Exception as exc:
        log.debug("cleanup_input_file(%s): %s", input_file_name, exc)
