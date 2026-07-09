"""Tests for the Gemini Batch API path in tgw.alt_text and tgw.apis.google_genai.

All tests run fully offline — Google API calls are mocked via monkeypatch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from tgw.apis.google_genai import (
    BATCH_IMAGES_PER_TASK,
    build_alt_text_task,
    parse_batch_results,
    write_batch_jsonl,
)

# ---------------------------------------------------------------------------
# Helpers (shared with test_alt_text.py, duplicated for isolation)
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path, runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    rt = runtime_root or (tmp_path / "runtime")
    return {
        "itemdata_root": tmp_path / "ItemData",
        "pretty": False,
        "secrets_root": tmp_path / "secrets",
        "raw": {"runtime_root": str(rt)},
    }


def _make_item(cfg: Dict[str, Any], sku: str, extra: dict | None = None) -> Path:
    sku_dir = Path(cfg["itemdata_root"]) / sku
    sku_dir.mkdir(parents=True, exist_ok=True)
    doc = {"sku": sku, **(extra or {})}
    p = sku_dir / f"{sku}.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _add_photo(cfg: Dict[str, Any], sku: str, name: str | None = None) -> Path:
    sku_dir = Path(cfg["itemdata_root"]) / sku
    fname = name or f"{sku}.jpg"
    p = sku_dir / fname
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
    return p


# ---------------------------------------------------------------------------
# Tests for tgw.apis.google_genai — pure functions (no SDK)
# ---------------------------------------------------------------------------


class TestBuildAltTextTask:
    def test_produces_valid_dict(self):
        task = build_alt_text_task(["AABB==", "CCDD=="], model="gemini-2.5-flash-lite")
        assert isinstance(task, dict)
        assert "models/" in task["model"]
        assert "contents" in task
        assert task["generation_config"]["response_mime_type"] == "application/json"

    def test_inline_data_present_for_each_image(self):
        task = build_alt_text_task(["IMG1==", "IMG2==", "IMG3=="])
        parts = task["contents"][0]["parts"]
        # First N parts are images, last part is text prompt
        image_parts = [p for p in parts if "inline_data" in p]
        assert len(image_parts) == 3
        assert image_parts[0]["inline_data"]["data"] == "IMG1=="

    def test_text_prompt_mentions_count(self):
        task = build_alt_text_task(["A==", "B=="])
        text_parts = [p["text"] for p in task["contents"][0]["parts"] if "text" in p]
        assert any("2" in t for t in text_parts)

    def test_model_prefixed_with_models_slash(self):
        task = build_alt_text_task(["A=="], model="gemini-2.5-flash-lite")
        assert task["model"] == "models/gemini-2.5-flash-lite"

    def test_system_instruction_present(self):
        task = build_alt_text_task(["A=="])
        assert "system_instruction" in task
        parts = task["system_instruction"]["parts"]
        assert any(p.get("text") for p in parts)

    def test_temperature_is_zero(self):
        task = build_alt_text_task(["A=="])
        assert task["generation_config"]["temperature"] == 0.0


class TestWriteBatchJsonl:
    def test_one_line_per_task(self, tmp_path):
        tasks = [{"model": "a", "contents": []}, {"model": "b", "contents": []}]
        path = tmp_path / "out.jsonl"
        write_batch_jsonl(tasks, path)
        lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 2

    def test_each_line_is_valid_json(self, tmp_path):
        tasks = [build_alt_text_task(["AABB=="]), build_alt_text_task(["CCDD=="])]
        path = tmp_path / "out.jsonl"
        write_batch_jsonl(tasks, path)
        for line in path.read_text().splitlines():
            if line.strip():
                obj = json.loads(line)
                assert "model" in obj


class TestParseBatchResults:
    def _make_line(self, items: list) -> str:
        return json.dumps({
            "response": {
                "candidates": [{
                    "content": {
                        "parts": [{"text": json.dumps(items)}],
                        "role": "model",
                    }
                }]
            }
        })

    def test_success_returns_items_list(self):
        items = [{"index": 0, "alt_text": "A watch", "seo_caption": "Elgin watch."}]
        raw = (self._make_line(items) + "\n").encode()
        results = parse_batch_results(raw)
        assert len(results) == 1
        assert results[0][0]["alt_text"] == "A watch"

    def test_error_line_returns_none(self):
        error_line = json.dumps({"error": {"code": 400, "message": "bad request"}})
        raw = (error_line + "\n").encode()
        results = parse_batch_results(raw)
        assert results[0] is None

    def test_invalid_json_line_returns_none(self):
        raw = b"not json at all\n"
        results = parse_batch_results(raw)
        assert results[0] is None

    def test_multiple_tasks_parsed_in_order(self):
        items_a = [{"index": 0, "alt_text": "A", "seo_caption": ""}]
        items_b = [{"index": 0, "alt_text": "B", "seo_caption": ""}]
        raw = (self._make_line(items_a) + "\n" + self._make_line(items_b) + "\n").encode()
        results = parse_batch_results(raw)
        assert len(results) == 2
        assert results[0][0]["alt_text"] == "A"
        assert results[1][0]["alt_text"] == "B"

    def test_empty_input_returns_empty_list(self):
        assert parse_batch_results(b"") == []
        assert parse_batch_results(b"\n\n") == []

    def test_items_wrapped_in_dict_unwrapped(self):
        items = {"items": [{"index": 0, "alt_text": "X", "seo_caption": ""}]}
        raw = (json.dumps({
            "response": {
                "candidates": [{
                    "content": {"parts": [{"text": json.dumps(items)}], "role": "model"}
                }]
            }
        }) + "\n").encode()
        results = parse_batch_results(raw)
        assert results[0][0]["alt_text"] == "X"


# ---------------------------------------------------------------------------
# Tests for cmd_alt_text_gemini_batch
# ---------------------------------------------------------------------------


class TestCmdAltTextGeminiBatch:
    def _setup(self, tmp_path, skus=("tgw001", "tgw002", "tgw003"), add_photos=True):
        cfg = _make_cfg(tmp_path)
        for sku in skus:
            _make_item(cfg, sku)
            if add_photos:
                _add_photo(cfg, sku)
        return cfg

    def _mock_genai(self, monkeypatch, submitted_job_name="batches/test-job-001"):
        """Patch out all Google API calls."""
        monkeypatch.setattr("tgw.alt_text._encode_resized", lambda p, max_px=512: "AABB==")
        monkeypatch.setattr("tgw.image_hash.compute_dhash", lambda p: "")
        monkeypatch.setattr("tgw.image_hash.lookup_hash", lambda h, t: None)
        monkeypatch.setattr("tgw.image_hash.store_hash", lambda *a, **kw: None)

        def _fake_submit(tasks, model, cfg, tmpdir):
            return submitted_job_name, "files/test-input"

        def _fake_poll(job_name, cfg, poll_interval_s=60, timeout_s=14400):
            return "JOB_STATE_SUCCEEDED_COMPLETED"

        def _fake_download(job_name, cfg):
            # Return one line per task with a result for each SKU in the task
            # We need to figure out how many SKUs per task based on the submitted tasks
            return b""  # will be overridden per test

        monkeypatch.setattr("tgw.alt_text.submit_batch", _fake_submit)
        monkeypatch.setattr("tgw.alt_text.poll_batch", _fake_poll)
        monkeypatch.setattr("tgw.alt_text.cleanup_input_file", lambda *a, **kw: None)

        return {"submitted_job_name": submitted_job_name}

    def _make_result_line(self, n: int, alt_prefix: str = "Product ") -> bytes:
        items = [
            {"index": i, "alt_text": f"{alt_prefix}{i}", "seo_caption": f"Caption {i}."}
            for i in range(n)
        ]
        return (json.dumps({
            "response": {
                "candidates": [{
                    "content": {"parts": [{"text": json.dumps(items)}], "role": "model"}
                }]
            }
        }) + "\n").encode()

    def test_dry_run_no_api_calls(self, tmp_path, monkeypatch):
        from tgw.alt_text import cmd_alt_text_gemini_batch

        cfg = self._setup(tmp_path, skus=("tgw001", "tgw002"))
        monkeypatch.setattr("tgw.alt_text._encode_resized", lambda p, max_px=512: "AABB==")
        monkeypatch.setattr("tgw.image_hash.compute_dhash", lambda p: "")
        monkeypatch.setattr("tgw.image_hash.lookup_hash", lambda h, t: None)
        monkeypatch.setattr("tgw.image_hash.store_hash", lambda *a, **kw: None)

        api_called = []
        monkeypatch.setattr("tgw.alt_text.submit_batch", lambda *a, **kw: api_called.append(1) or ("j", "f"))

        result = cmd_alt_text_gemini_batch(cfg, dry_run=True)

        assert result["ok"] is True
        assert result["dry_run"] is True
        assert result["eligible"] == 2
        assert result["would_submit"] == 2
        assert api_called == []

    def test_dry_run_returns_chunk_count(self, tmp_path, monkeypatch):
        from tgw.alt_text import cmd_alt_text_gemini_batch

        # Create more items than BATCH_IMAGES_PER_TASK to verify chunk count
        skus = tuple(f"tgw{i:03d}" for i in range(BATCH_IMAGES_PER_TASK + 5))
        cfg = self._setup(tmp_path, skus=skus)
        monkeypatch.setattr("tgw.alt_text._encode_resized", lambda p, max_px=512: "AABB==")
        monkeypatch.setattr("tgw.image_hash.compute_dhash", lambda p: "")
        monkeypatch.setattr("tgw.image_hash.lookup_hash", lambda h, t: None)
        monkeypatch.setattr("tgw.image_hash.store_hash", lambda *a, **kw: None)

        result = cmd_alt_text_gemini_batch(cfg, dry_run=True)

        assert result["chunk_count"] == 2  # 45 items → 2 chunks (40 + 5)

    def test_no_eligible_returns_early(self, tmp_path):
        from tgw.alt_text import cmd_alt_text_gemini_batch

        cfg = _make_cfg(tmp_path)
        Path(cfg["itemdata_root"]).mkdir(parents=True, exist_ok=True)

        result = cmd_alt_text_gemini_batch(cfg)
        assert result["ok"] is True
        assert result["eligible"] == 0

    def test_skips_items_with_existing_alt_text(self, tmp_path, monkeypatch):
        from tgw.alt_text import cmd_alt_text_gemini_batch

        cfg = self._setup(tmp_path, skus=("tgw001", "tgw002"))
        # Mark tgw001 as already done
        jp = Path(cfg["itemdata_root"]) / "tgw001" / "tgw001.json"
        item = json.loads(jp.read_text())
        item["draft_listing"] = {"alt_text": "already done"}
        jp.write_text(json.dumps(item))

        monkeypatch.setattr("tgw.alt_text._encode_resized", lambda p, max_px=512: "AABB==")
        monkeypatch.setattr("tgw.image_hash.compute_dhash", lambda p: "")
        monkeypatch.setattr("tgw.image_hash.lookup_hash", lambda h, t: None)
        monkeypatch.setattr("tgw.image_hash.store_hash", lambda *a, **kw: None)

        result = cmd_alt_text_gemini_batch(cfg, dry_run=True)
        assert result["eligible"] == 1  # only tgw002

    def test_cached_images_skipped_from_api(self, tmp_path, monkeypatch):
        from tgw.alt_text import cmd_alt_text_gemini_batch

        cfg = self._setup(tmp_path, skus=("tgw001", "tgw002"))
        monkeypatch.setattr("tgw.alt_text._encode_resized", lambda p, max_px=512: "AABB==")
        monkeypatch.setattr("tgw.image_hash.compute_dhash", lambda p: "deadbeef")

        # Both images are in cache
        def _cached(h, t):
            return {"alt_text": "Cached alt", "seo_caption": "Cached caption."}

        monkeypatch.setattr("tgw.image_hash.lookup_hash", _cached)
        monkeypatch.setattr("tgw.image_hash.store_hash", lambda *a, **kw: None)

        result = cmd_alt_text_gemini_batch(cfg, dry_run=True)
        assert result["skipped_cached"] == 2
        assert result["would_submit"] == 0

    def test_limit_caps_eligible(self, tmp_path, monkeypatch):
        from tgw.alt_text import cmd_alt_text_gemini_batch

        cfg = self._setup(tmp_path, skus=("tgw001", "tgw002", "tgw003"))
        monkeypatch.setattr("tgw.alt_text._encode_resized", lambda p, max_px=512: "AABB==")
        monkeypatch.setattr("tgw.image_hash.compute_dhash", lambda p: "")
        monkeypatch.setattr("tgw.image_hash.lookup_hash", lambda h, t: None)
        monkeypatch.setattr("tgw.image_hash.store_hash", lambda *a, **kw: None)

        result = cmd_alt_text_gemini_batch(cfg, dry_run=True, limit=2)
        assert result["eligible"] == 2

    def test_successful_run_writes_alt_text(self, tmp_path, monkeypatch):
        from tgw.alt_text import cmd_alt_text_gemini_batch

        cfg = self._setup(tmp_path, skus=("tgw001", "tgw002"))
        self._mock_genai(monkeypatch)

        result_line = self._make_result_line(2)
        monkeypatch.setattr("tgw.alt_text.download_batch_output", lambda *a, **kw: result_line)

        result = cmd_alt_text_gemini_batch(cfg)

        assert result["ok"] is True
        assert result["processed"] == 2
        assert result["errors"] == 0

        for sku in ("tgw001", "tgw002"):
            jp = Path(cfg["itemdata_root"]) / sku / f"{sku}.json"
            item = json.loads(jp.read_text())
            assert item["draft_listing"]["alt_text"].startswith("Product ")

    def test_failed_batch_returns_error(self, tmp_path, monkeypatch):
        from tgw.alt_text import cmd_alt_text_gemini_batch

        cfg = self._setup(tmp_path, skus=("tgw001",))
        monkeypatch.setattr("tgw.alt_text._encode_resized", lambda p, max_px=512: "AABB==")
        monkeypatch.setattr("tgw.image_hash.compute_dhash", lambda p: "")
        monkeypatch.setattr("tgw.image_hash.lookup_hash", lambda h, t: None)
        monkeypatch.setattr("tgw.image_hash.store_hash", lambda *a, **kw: None)
        monkeypatch.setattr("tgw.alt_text.submit_batch", lambda *a, **kw: ("batches/fail-job", "files/x"))
        monkeypatch.setattr("tgw.alt_text.poll_batch", lambda *a, **kw: "JOB_STATE_FAILED")
        monkeypatch.setattr("tgw.alt_text.cleanup_input_file", lambda *a, **kw: None)

        result = cmd_alt_text_gemini_batch(cfg)
        assert result["ok"] is False
        assert "FAILED" in result["error"]

    def test_error_task_collected_not_raised(self, tmp_path, monkeypatch):
        from tgw.alt_text import cmd_alt_text_gemini_batch

        cfg = self._setup(tmp_path, skus=("tgw001", "tgw002"))
        self._mock_genai(monkeypatch)

        # Task 0 fails (None), task for tgw002 doesn't exist either — both error
        monkeypatch.setattr("tgw.alt_text.download_batch_output", lambda *a, **kw: b"not json\n")

        result = cmd_alt_text_gemini_batch(cfg)
        assert result["ok"] is True  # command succeeds; individual errors collected
        assert result["errors"] >= 1

    def test_resume_from_state_file(self, tmp_path, monkeypatch):
        from tgw.alt_text import _batch_state_path, cmd_alt_text_gemini_batch

        cfg = self._setup(tmp_path, skus=("tgw001",))
        monkeypatch.setattr("tgw.alt_text._encode_resized", lambda p, max_px=512: "AABB==")
        monkeypatch.setattr("tgw.image_hash.compute_dhash", lambda p: "")
        monkeypatch.setattr("tgw.image_hash.lookup_hash", lambda h, t: None)
        monkeypatch.setattr("tgw.image_hash.store_hash", lambda *a, **kw: None)

        # Pre-create state file simulating a submitted-but-not-completed job
        state_path = _batch_state_path(cfg)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            "job_name": "batches/resume-test",
            "input_file_name": "files/resume-input",
            "model": "gemini-2.5-flash-lite",
            "status": "PROCESSING",
            "chunks": [["tgw001"]],
        }))

        polled = []

        def _fake_poll(job_name, cfg, poll_interval_s=60, timeout_s=14400):
            polled.append(job_name)
            return "COMPLETED"

        monkeypatch.setattr("tgw.alt_text.poll_batch", _fake_poll)
        monkeypatch.setattr("tgw.alt_text.download_batch_output",
                            lambda *a, **kw: self._make_result_line(1))
        monkeypatch.setattr("tgw.alt_text.cleanup_input_file", lambda *a, **kw: None)

        result = cmd_alt_text_gemini_batch(cfg)
        assert result["ok"] is True
        assert polled == ["batches/resume-test"]  # resumed the existing job

    def test_state_file_deleted_after_success(self, tmp_path, monkeypatch):
        from tgw.alt_text import _batch_state_path, cmd_alt_text_gemini_batch

        cfg = self._setup(tmp_path, skus=("tgw001",))
        self._mock_genai(monkeypatch)
        monkeypatch.setattr("tgw.alt_text.download_batch_output",
                            lambda *a, **kw: self._make_result_line(1))

        cmd_alt_text_gemini_batch(cfg)
        assert not _batch_state_path(cfg).exists()

    def test_state_file_created_after_submit(self, tmp_path, monkeypatch):
        from tgw.alt_text import _batch_state_path, cmd_alt_text_gemini_batch

        cfg = self._setup(tmp_path, skus=("tgw001",))
        monkeypatch.setattr("tgw.alt_text._encode_resized", lambda p, max_px=512: "AABB==")
        monkeypatch.setattr("tgw.image_hash.compute_dhash", lambda p: "")
        monkeypatch.setattr("tgw.image_hash.lookup_hash", lambda h, t: None)
        monkeypatch.setattr("tgw.image_hash.store_hash", lambda *a, **kw: None)
        monkeypatch.setattr("tgw.alt_text.submit_batch",
                            lambda *a, **kw: ("batches/state-test", "files/x"))

        # Interrupt after submit by making poll raise
        monkeypatch.setattr("tgw.alt_text.poll_batch",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("interrupted")))
        monkeypatch.setattr("tgw.alt_text.cleanup_input_file", lambda *a, **kw: None)

        with pytest.raises(RuntimeError, match="interrupted"):
            cmd_alt_text_gemini_batch(cfg)

        state_path = _batch_state_path(cfg)
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert state["job_name"] == "batches/state-test"
        assert state["status"] == "PROCESSING"


# ---------------------------------------------------------------------------
# Tests for load_google_key
# ---------------------------------------------------------------------------


class TestLoadGoogleKey:
    """Single-facility env-var convention (tgw.apis.secrets, #1252/#1253) —
    load_google_key() no longer reads a credentials.json file at all."""

    def test_loads_from_env_var(self, monkeypatch):
        from tgw.apis.google_genai import load_google_key

        monkeypatch.setenv("GOOGLE_API_KEY", "env-key-456")
        assert load_google_key({}) == "env-key-456"

    def test_raises_when_key_absent(self, monkeypatch):
        from tgw.apis.google_genai import load_google_key

        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="GOOGLE_API_KEY not set"):
            load_google_key({})
