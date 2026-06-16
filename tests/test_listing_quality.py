"""Tests for tgw.listing_quality — no_brand, no_model, and price scoring."""
from tgw.listing_quality import score_draft


def _item(title="A Long Enough Title For Testing Purposes Here", specs=None, pl=None,
          price_comps=None, photos=3):
    return {
        "draft_listing": {
            "title": title,
            "item_specifics": specs or {},
            "aspects_required_total": 0,
            "aspects_required_filled": 0,
            "aspects_recommended_total": 0,
            "aspects_recommended_filled": 0,
        },
        "product_lookup": pl or {},
        "ebay_offer": {"price_comps": price_comps or {"count": 5}},
        "ebay_photos": ["x"] * photos,
    }


# ── no_brand ──────────────────────────────────────────────────────────────────

class TestNoBrand:
    def test_real_brand_in_specs_and_title(self):
        item = _item("HP LaserJet Pro Printer Used", specs={"Brand": "HP"})
        result = score_draft(item)
        assert "no_brand" not in result.flags
        assert result.brand_pts == 25

    def test_real_brand_in_specs_not_in_title(self):
        item = _item("Laser Printer Used Office Grade", specs={"Brand": "HP"})
        result = score_draft(item)
        assert "no_brand" not in result.flags
        assert result.brand_pts == 12  # known but not injected

    def test_no_brand_truly_missing(self):
        item = _item("Generic USB Cable 6 Foot Fast Charge", specs={})
        result = score_draft(item)
        assert "no_brand" in result.flags
        assert result.brand_pts == 0

    def test_no_brand_suppressed_when_unbranded_in_title(self):
        # False-positive fix: "Unbranded" is a generic but IS in the title — don't flag
        item = _item("Unbranded USB-C Cable 6ft Fast Charging", specs={"Brand": "Unbranded"})
        result = score_draft(item)
        assert "no_brand" not in result.flags

    def test_no_brand_suppressed_when_does_not_apply_in_title(self):
        item = _item("Does Not Apply Generic Cable Lot", specs={"Brand": "Does Not Apply"})
        result = score_draft(item)
        assert "no_brand" not in result.flags

    def test_no_brand_still_flagged_when_generic_not_in_title(self):
        # Generic brand in specs but NOT in title → still flag
        item = _item("USB Cable 6 Foot Fast Charge White", specs={"Brand": "Unbranded"})
        result = score_draft(item)
        assert "no_brand" in result.flags

    def test_pl_brand_takes_priority(self):
        item = _item("Samsung Galaxy Smartphone Screen", pl={"brand": "Samsung"})
        result = score_draft(item)
        assert "no_brand" not in result.flags
        assert result.brand_pts == 25

    def test_spec_brand_lowercase_key(self):
        item = _item("Casio G-Shock Watch Sport Digital", specs={"brand": "Casio"})
        result = score_draft(item)
        assert "no_brand" not in result.flags


# ── no_model ─────────────────────────────────────────────────────────────────

class TestNoModel:
    def test_no_model_flagged_when_missing(self):
        item = _item("HP LaserJet Pro Printer Office", specs={"Brand": "HP"})
        result = score_draft(item)
        assert "no_model" in result.flags

    def test_no_model_not_flagged_with_mpn_in_title(self):
        item = _item("HP LaserJet Pro M404n Printer", specs={"Brand": "HP", "MPN": "M404n"})
        result = score_draft(item)
        assert "no_model" not in result.flags
        assert result.model_pts == 10

    def test_no_model_not_flagged_with_mpn_not_in_title(self):
        item = _item("HP LaserJet Pro Printer Office", specs={"Brand": "HP", "MPN": "M404n"})
        result = score_draft(item)
        assert "no_model" not in result.flags
        assert result.model_pts == 5

    def test_no_model_suppressed_by_does_not_apply(self):
        item = _item("Unbranded USB Cable 6ft Fast", specs={"Brand": "Unbranded", "Model": "Does Not Apply"})
        result = score_draft(item)
        assert "no_model" not in result.flags

    def test_no_model_suppressed_by_unknown(self):
        item = _item("Vintage Ceramic Vase Blue White Decor", specs={"Model": "Unknown"})
        result = score_draft(item)
        assert "no_model" not in result.flags

    def test_no_model_suppressed_by_na(self):
        item = _item("Generic USB Cable Fast Charge White", specs={"Model": "N/A"})
        result = score_draft(item)
        assert "no_model" not in result.flags

    def test_no_model_flagged_with_generic_brand_in_mpn(self):
        # "Unbranded" in MPN field is not a real model — should still flag
        item = _item("USB Cable Lot Mixed Types Various", specs={"MPN": "Unbranded"})
        result = score_draft(item)
        assert "no_model" in result.flags
