"""Tests for tgw.printing — Phase 1 offline (PP-ADD-009 / PP-FULFILLMENT-001).

All file I/O uses tmp_path.
CUPS is always mocked — no printer hardware required.
Tests generate real PDFs via reportlab/qrcode; skip if printing deps not installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Skip the whole module if printing deps aren't installed
reportlab = pytest.importorskip("reportlab", reason="printing deps not installed")
qrcode = pytest.importorskip("qrcode", reason="printing deps not installed")

from tgw.printing import build_label_pdf, build_picklist_pdf, cups_print  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ROWS = [
    {"location": "A1", "sku": "tgw20260101000001", "title": "Vintage pocket watch", "ebay_id": "123"},
    {"location": "A1", "sku": "tgw20260101000002", "title": "Silver brooch", "ebay_id": ""},
    {"location": "B3", "sku": "tgw20260101000003", "title": "Camera lens", "ebay_id": "456"},
    {"location": "", "sku": "tgw20260101000004", "title": "Unlocated widget", "ebay_id": ""},
]


# ---------------------------------------------------------------------------
# build_picklist_pdf
# ---------------------------------------------------------------------------

class TestBuildPicklistPdf:
    def test_creates_file(self, tmp_path):
        out = tmp_path / "list.pdf"
        build_picklist_pdf(_ROWS, out)
        assert out.exists()

    def test_returns_path(self, tmp_path):
        out = tmp_path / "list.pdf"
        result = build_picklist_pdf(_ROWS, out)
        assert result == out

    def test_output_is_valid_pdf(self, tmp_path):
        out = tmp_path / "list.pdf"
        build_picklist_pdf(_ROWS, out)
        assert out.read_bytes()[:4] == b"%PDF"

    def test_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "deep" / "nested" / "list.pdf"
        build_picklist_pdf(_ROWS, out)
        assert out.exists()

    def test_empty_rows_produces_pdf(self, tmp_path):
        out = tmp_path / "empty.pdf"
        build_picklist_pdf([], out)
        assert out.read_bytes()[:4] == b"%PDF"

    def test_accepts_string_path(self, tmp_path):
        out = str(tmp_path / "list.pdf")
        build_picklist_pdf(_ROWS, out)
        assert Path(out).exists()

    def test_single_row(self, tmp_path):
        out = tmp_path / "single.pdf"
        build_picklist_pdf([_ROWS[0]], out)
        assert out.read_bytes()[:4] == b"%PDF"

    def test_rows_with_long_titles(self, tmp_path):
        rows = [{"location": "A1", "sku": "tgw20260101000001",
                  "title": "A" * 200, "ebay_id": "9999"}]
        out = tmp_path / "long.pdf"
        build_picklist_pdf(rows, out)
        assert out.exists()

    def test_custom_title_accepted(self, tmp_path):
        out = tmp_path / "custom.pdf"
        build_picklist_pdf(_ROWS, out, title="Test Picklist")
        assert out.read_bytes()[:4] == b"%PDF"

    def test_unlocated_items_included(self, tmp_path):
        rows = [{"location": "", "sku": "tgw20260101000004",
                  "title": "No location", "ebay_id": ""}]
        out = tmp_path / "unloc.pdf"
        build_picklist_pdf(rows, out)
        assert out.exists()


# ---------------------------------------------------------------------------
# build_label_pdf
# ---------------------------------------------------------------------------

class TestBuildLabelPdf:
    def test_creates_file(self, tmp_path):
        out = tmp_path / "label.pdf"
        build_label_pdf("tgw20260101000001", "Vintage watch", "A1", out)
        assert out.exists()

    def test_returns_path(self, tmp_path):
        out = tmp_path / "label.pdf"
        result = build_label_pdf("tgw20260101000001", "Vintage watch", "A1", out)
        assert result == out

    def test_output_is_valid_pdf(self, tmp_path):
        out = tmp_path / "label.pdf"
        build_label_pdf("tgw20260101000001", "Vintage watch", "A1", out)
        assert out.read_bytes()[:4] == b"%PDF"

    def test_empty_title_accepted(self, tmp_path):
        out = tmp_path / "label.pdf"
        build_label_pdf("tgw20260101000001", "", "A1", out)
        assert out.exists()

    def test_empty_location_accepted(self, tmp_path):
        out = tmp_path / "label.pdf"
        build_label_pdf("tgw20260101000001", "Watch", "", out)
        assert out.exists()

    def test_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "labels" / "sku.pdf"
        build_label_pdf("tgw20260101000001", "Item", "B2", out)
        assert out.exists()

    def test_long_title_truncated_gracefully(self, tmp_path):
        out = tmp_path / "label.pdf"
        build_label_pdf("tgw20260101000001", "X" * 200, "A1", out)
        assert out.read_bytes()[:4] == b"%PDF"

    def test_accepts_string_path(self, tmp_path):
        out = str(tmp_path / "label.pdf")
        build_label_pdf("tgw20260101000001", "Watch", "A1", out)
        assert Path(out).exists()


# ---------------------------------------------------------------------------
# cups_print
# ---------------------------------------------------------------------------

class TestCupsPrint:
    def test_returns_true_on_success(self, tmp_path):
        fake_pdf = tmp_path / "file.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert cups_print(fake_pdf, "testqueue") is True

    def test_calls_lpr_with_queue_and_path(self, tmp_path):
        fake_pdf = tmp_path / "file.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            cups_print(fake_pdf, "myprinter")
        call_args = mock_run.call_args[0][0]
        assert "lpr" in call_args
        assert "-P" in call_args
        assert "myprinter" in call_args
        assert str(fake_pdf) in call_args

    def test_returns_false_on_nonzero_returncode(self, tmp_path):
        fake_pdf = tmp_path / "file.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert cups_print(fake_pdf, "testqueue") is False

    def test_returns_false_when_lpr_not_found(self, tmp_path):
        fake_pdf = tmp_path / "file.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4")
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert cups_print(fake_pdf, "testqueue") is False

    def test_returns_false_on_timeout(self, tmp_path):
        import subprocess
        fake_pdf = tmp_path / "file.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4")
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="lpr", timeout=15)):
            assert cups_print(fake_pdf, "testqueue") is False


# ---------------------------------------------------------------------------
# cmd_picklist --pdf integration
# ---------------------------------------------------------------------------

_LIST_ITEMS_PATCH = "tgw.api.list_items"


class TestCmdPicklistPdf:
    def _stub_list(self):
        return lambda cfg, **kw: {"ok": True, "count": len(_ROWS), "items": [
            {"sku": r["sku"], "title": r["title"], "location": r["location"],
             "ebay_listing": {"listing_id": r["ebay_id"]} if r["ebay_id"] else {}}
            for r in _ROWS
        ]}

    def test_pdf_flag_generates_file(self, tmp_path, monkeypatch):
        import tgw.api as api
        monkeypatch.setattr(api, "list_items", self._stub_list())
        out = tmp_path / "pick.pdf"
        result = api.cmd_picklist({}, pdf=True, output=str(out))
        assert result["ok"] is True
        assert "pdf" in result
        assert out.exists()
        assert out.read_bytes()[:4] == b"%PDF"

    def test_pdf_path_in_result(self, tmp_path, monkeypatch):
        import tgw.api as api
        monkeypatch.setattr(api, "list_items", self._stub_list())
        out = tmp_path / "pick.pdf"
        result = api.cmd_picklist({}, pdf=True, output=str(out))
        assert result["pdf"] == str(out)

    def test_no_pdf_flag_no_pdf_key(self, monkeypatch):
        import tgw.api as api
        monkeypatch.setattr(api, "list_items", self._stub_list())
        result = api.cmd_picklist({})
        assert "pdf" not in result
        assert "pdf_error" not in result

    def test_pdf_with_cups_config_calls_cups(self, tmp_path, monkeypatch):
        import tgw.api as api
        monkeypatch.setattr(api, "list_items", self._stub_list())
        out = tmp_path / "pick.pdf"
        cfg = {"print_cups_queue": "labelprinter"}
        with patch("tgw.printing.cups_print", return_value=True) as mock_cups:
            result = api.cmd_picklist(cfg, pdf=True, output=str(out))
        assert result.get("cups_sent") is True
        assert result.get("cups_queue") == "labelprinter"
        mock_cups.assert_called_once()

    def test_pdf_without_cups_config_not_sent(self, tmp_path, monkeypatch):
        import tgw.api as api
        monkeypatch.setattr(api, "list_items", self._stub_list())
        out = tmp_path / "pick.pdf"
        result = api.cmd_picklist({}, pdf=True, output=str(out))
        assert "cups_sent" not in result

    def test_pdf_default_output_path_is_set(self, tmp_path, monkeypatch):
        """When --output is omitted, result['pdf'] is a temp file path."""
        import tgw.api as api
        monkeypatch.setattr(api, "list_items", self._stub_list())
        result = api.cmd_picklist({}, pdf=True)
        assert result["ok"] is True
        assert "pdf" in result
        assert Path(result["pdf"]).exists()
        # clean up
        Path(result["pdf"]).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# cmd_print_label integration
# ---------------------------------------------------------------------------

class TestCmdPrintLabel:
    def _make_item(self, cfg, sku, title="Silver watch", location="A1"):
        d = Path(cfg["itemdata_root"]) / sku
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{sku}.json").write_text(
            json.dumps({"sku": sku, "title": title, "location": location}),
            encoding="utf-8",
        )

    def _make_cfg(self, tmp_path):
        return {"itemdata_root": tmp_path / "ItemData"}

    def test_generates_pdf(self, tmp_path):
        import tgw.api as api
        cfg = self._make_cfg(tmp_path)
        sku = "tgw20260101000001"
        self._make_item(cfg, sku)
        out = tmp_path / "label.pdf"
        result = api.cmd_print_label(cfg, sku, output=str(out))
        assert result["ok"] is True
        assert out.exists()
        assert out.read_bytes()[:4] == b"%PDF"

    def test_returns_sku_and_path(self, tmp_path):
        import tgw.api as api
        cfg = self._make_cfg(tmp_path)
        sku = "tgw20260101000001"
        self._make_item(cfg, sku)
        out = tmp_path / "label.pdf"
        result = api.cmd_print_label(cfg, sku, output=str(out))
        assert result["sku"] == sku
        assert result["pdf"] == str(out)

    def test_unknown_sku_returns_error(self, tmp_path):
        import tgw.api as api
        cfg = self._make_cfg(tmp_path)
        result = api.cmd_print_label(cfg, "tgw99999999999999999")
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_cups_sent_false_without_config(self, tmp_path):
        import tgw.api as api
        cfg = self._make_cfg(tmp_path)
        sku = "tgw20260101000001"
        self._make_item(cfg, sku)
        out = tmp_path / "label.pdf"
        result = api.cmd_print_label(cfg, sku, output=str(out))
        assert result["cups_sent"] is False

    def test_cups_sent_when_configured(self, tmp_path):
        import tgw.api as api
        cfg = self._make_cfg(tmp_path)
        cfg["print_cups_queue"] = "labelprinter"
        sku = "tgw20260101000001"
        self._make_item(cfg, sku)
        out = tmp_path / "label.pdf"
        with patch("tgw.printing.cups_print", return_value=True) as mock_cups:
            result = api.cmd_print_label(cfg, sku, output=str(out))
        assert result["cups_sent"] is True
        assert result["cups_queue"] == "labelprinter"
        mock_cups.assert_called_once()

    def test_default_output_path_used_when_omitted(self, tmp_path):
        import tgw.api as api
        cfg = self._make_cfg(tmp_path)
        sku = "tgw20260101000001"
        self._make_item(cfg, sku)
        result = api.cmd_print_label(cfg, sku)
        assert result["ok"] is True
        assert Path(result["pdf"]).exists()
        Path(result["pdf"]).unlink(missing_ok=True)

    def test_item_with_no_location(self, tmp_path):
        import tgw.api as api
        cfg = self._make_cfg(tmp_path)
        sku = "tgw20260101000001"
        self._make_item(cfg, sku, location="")
        out = tmp_path / "label.pdf"
        result = api.cmd_print_label(cfg, sku, output=str(out))
        assert result["ok"] is True
        assert out.read_bytes()[:4] == b"%PDF"
