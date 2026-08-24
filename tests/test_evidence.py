"""Tests for tgw.workflow.evidence — coding and TGW evidence source functions.

Covers all six FingerprintResult values across every function:
  TRUE, FALSE, UNKNOWN, STALE, CONTRADICTORY, NOT_APPLICABLE
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import pytest

from tgw.workflow_kernel.contracts import EvidenceAssertion, EvidenceReference, FingerprintResult
from tgw.workflow.evidence import (  # noqa: E402
    assert_ai_identified,
    assert_condition,
    assert_controller_verified,
    assert_draft_generated,
    assert_implemented,
    assert_item_has_photos,
    assert_linted,
    assert_photos_uploaded,
    assert_priced,
    assert_published,
    assert_reviewed,
    assert_staged,
    assert_tested,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ae(condition_id, result, reasons=(), evidence=()):
    """Shorthand assertion builder for comparisons."""
    return EvidenceAssertion(condition_id, result, reasons, evidence)


# ===================================================================
# Generic factory — assert_condition
# ===================================================================

class TestAssertCondition:
    def test_true(self):
        r = assert_condition("x", FingerprintResult.TRUE, reasons=("ok",))
        assert r == _ae("x", FingerprintResult.TRUE, ("ok",))

    def test_false(self):
        r = assert_condition("x", FingerprintResult.FALSE, reasons=("bad",))
        assert r == _ae("x", FingerprintResult.FALSE, ("bad",))

    def test_unknown(self):
        r = assert_condition("x", FingerprintResult.UNKNOWN)
        assert r == _ae("x", FingerprintResult.UNKNOWN)

    def test_rejects_non_fingerprint(self):
        with pytest.raises(TypeError):
            assert_condition("x", True)  # type: ignore[arg-type]

    def test_with_evidence(self):
        ref = EvidenceReference("id1", "src", "gen1")
        r = assert_condition("x", FingerprintResult.TRUE, evidence=(ref,))
        assert r.evidence == (ref,)


# ===================================================================
# Coding evidence sources
# ===================================================================

class TestAssertTested:
    def test_true(self):
        r = assert_tested(0, "14 passed")
        assert r.result is FingerprintResult.TRUE
        assert r.reasons == ("14 passed",)
        assert len(r.evidence) == 1
        assert r.evidence[0].source_class == "pytest"

    def test_false(self):
        r = assert_tested(1, "2 failed")
        assert r.result is FingerprintResult.FALSE
        assert "exit_code=1" in r.reasons[0]

    def test_unknown(self):
        r = assert_tested(None, "")
        assert r.result is FingerprintResult.UNKNOWN
        assert r.evidence == ()


class TestAssertLinted:
    def test_true(self):
        r = assert_linted(0)
        assert r.result is FingerprintResult.TRUE

    def test_false(self):
        r = assert_linted(1)
        assert r.result is FingerprintResult.FALSE
        assert "exit_code=1" in r.reasons[0]

    def test_unknown(self):
        r = assert_linted(None)
        assert r.result is FingerprintResult.UNKNOWN


class TestAssertReviewed:
    def test_true(self):
        r = assert_reviewed("approved")
        assert r.result is FingerprintResult.TRUE

    def test_false(self):
        r = assert_reviewed("changes_requested")
        assert r.result is FingerprintResult.FALSE

    def test_unknown_none(self):
        r = assert_reviewed(None)
        assert r.result is FingerprintResult.UNKNOWN

    def test_unknown_unrecognised(self):
        r = assert_reviewed("pending")
        assert r.result is FingerprintResult.UNKNOWN


class TestAssertControllerVerified:
    def test_true(self):
        r = assert_controller_verified(True)
        assert r.result is FingerprintResult.TRUE

    def test_false(self):
        r = assert_controller_verified(False)
        assert r.result is FingerprintResult.FALSE

    def test_unknown(self):
        r = assert_controller_verified(None)
        assert r.result is FingerprintResult.UNKNOWN


class TestAssertImplemented:
    def test_true(self):
        r = assert_implemented(True)
        assert r.result is FingerprintResult.TRUE

    def test_false(self):
        r = assert_implemented(False)
        assert r.result is FingerprintResult.FALSE

    def test_unknown(self):
        r = assert_implemented(None)
        assert r.result is FingerprintResult.UNKNOWN


# ===================================================================
# TGW evidence sources
# ===================================================================

class TestAssertItemHasPhotos:
    def test_true_with_images_list(self):
        r = assert_item_has_photos({"images": ["photo1.jpg"]})
        assert r.result is FingerprintResult.TRUE
        assert r.evidence[0].source_class == "item_data"

    def test_true_with_photos_list(self):
        r = assert_item_has_photos({"photos": ["a.jpg", "b.jpg"]})
        assert r.result is FingerprintResult.TRUE

    def test_true_with_image_string(self):
        r = assert_item_has_photos({"image": "cover.jpg"})
        assert r.result is FingerprintResult.TRUE

    def test_false_empty_field(self):
        r = assert_item_has_photos({"images": []})
        assert r.result is FingerprintResult.FALSE
        assert "images" in r.reasons[0]

    def test_unknown_none(self):
        r = assert_item_has_photos(None)
        assert r.result is FingerprintResult.UNKNOWN

    def test_unknown_no_image_field(self):
        r = assert_item_has_photos({"sku": "ABC123", "title": "Widget"})
        assert r.result is FingerprintResult.UNKNOWN

    def test_not_applicable_unlisted(self):
        r = assert_item_has_photos({"listing_status": "not_listed"})
        assert r.result is FingerprintResult.NOT_APPLICABLE

    def test_not_applicable_archived(self):
        r = assert_item_has_photos({"status": "archived"})
        assert r.result is FingerprintResult.NOT_APPLICABLE


class TestAssertPhotosUploaded:
    def test_true_ebay_photos(self):
        r = assert_photos_uploaded({"ebay_photos": ["https://img.ebay.com/1.jpg"]})
        assert r.result is FingerprintResult.TRUE

    def test_true_draft_image_urls(self):
        r = assert_photos_uploaded({"draft_listing": {"imageUrls": ["https://x.com/a.jpg"]}})
        assert r.result is FingerprintResult.TRUE

    def test_true_draft_image_urls_snake(self):
        r = assert_photos_uploaded({"draft_listing": {"image_urls": ["https://x.com/a.jpg"]}})
        assert r.result is FingerprintResult.TRUE

    def test_false_empty_ebay_photos(self):
        r = assert_photos_uploaded({"ebay_photos": []})
        assert r.result is FingerprintResult.FALSE

    def test_unknown_none(self):
        r = assert_photos_uploaded(None)
        assert r.result is FingerprintResult.UNKNOWN

    def test_unknown_no_evidence(self):
        r = assert_photos_uploaded({"sku": "X"})
        assert r.result is FingerprintResult.UNKNOWN

    def test_not_applicable_unlisted(self):
        r = assert_photos_uploaded({"listing_status": "not_listed"})
        assert r.result is FingerprintResult.NOT_APPLICABLE


class TestAssertAiIdentified:
    def test_true(self):
        r = assert_ai_identified({"ebay_category_id": "12345", "title": "Widget Pro"})
        assert r.result is FingerprintResult.TRUE

    def test_true_with_ebay_title(self):
        r = assert_ai_identified({"ebay_category_id": "999", "ebay_title": "Thing"})
        assert r.result is FingerprintResult.TRUE

    def test_false_no_category(self):
        r = assert_ai_identified({"title": "Widget"})
        assert r.result is FingerprintResult.FALSE
        assert "ebay_category_id missing" in r.reasons[0]

    def test_false_no_title(self):
        r = assert_ai_identified({"ebay_category_id": "123"})
        assert r.result is FingerprintResult.FALSE
        assert "title missing" in r.reasons[0]

    def test_false_both_missing_but_keys_present(self):
        r = assert_ai_identified({"ebay_category_id": "", "title": ""})
        assert r.result is FingerprintResult.FALSE

    def test_unknown_none(self):
        r = assert_ai_identified(None)
        assert r.result is FingerprintResult.UNKNOWN

    def test_unknown_no_keys(self):
        r = assert_ai_identified({"sku": "X"})
        assert r.result is FingerprintResult.UNKNOWN

    def test_not_applicable_unlisted(self):
        r = assert_ai_identified({"listing_status": "not_listed"})
        assert r.result is FingerprintResult.NOT_APPLICABLE


class TestAssertDraftGenerated:
    def test_true(self):
        r = assert_draft_generated({"draft_listing": {"category": "9355"}})
        assert r.result is FingerprintResult.TRUE

    def test_false_category_99(self):
        r = assert_draft_generated({"draft_listing": {"category": "99"}})
        assert r.result is FingerprintResult.FALSE
        assert "99" in r.reasons[0]

    def test_false_category_empty(self):
        r = assert_draft_generated({"draft_listing": {"category": ""}})
        assert r.result is FingerprintResult.FALSE

    def test_unknown_no_draft(self):
        r = assert_draft_generated({"sku": "X"})
        assert r.result is FingerprintResult.UNKNOWN

    def test_unknown_none(self):
        r = assert_draft_generated(None)
        assert r.result is FingerprintResult.UNKNOWN

    def test_not_applicable_unlisted(self):
        r = assert_draft_generated({"listing_status": "not_listed"})
        assert r.result is FingerprintResult.NOT_APPLICABLE


class TestAssertPriced:
    def test_true(self):
        r = assert_priced({"draft_listing": {"price": 29.99}})
        assert r.result is FingerprintResult.TRUE
        assert "29.99" in r.reasons[0]

    def test_true_integer_price(self):
        r = assert_priced({"draft_listing": {"price": 150}})
        assert r.result is FingerprintResult.TRUE

    def test_true_string_price(self):
        r = assert_priced({"draft_listing": {"price": "49.95"}})
        assert r.result is FingerprintResult.TRUE

    def test_false_zero_price(self):
        r = assert_priced({"draft_listing": {"price": 0}})
        assert r.result is FingerprintResult.FALSE

    def test_false_negative_price(self):
        r = assert_priced({"draft_listing": {"price": -5.00}})
        assert r.result is FingerprintResult.FALSE

    def test_stale_price_drift(self):
        """STALE when staged_price != draft price."""
        r = assert_priced({
            "draft_listing": {"price": 100.00},
            "staged_price": 95.00,
        })
        assert r.result is FingerprintResult.STALE
        assert "price_drift" in r.reasons[0]

    def test_contradictory_pipeline_error(self):
        """CONTRADICTORY when pipeline_error contains 'price'."""
        r = assert_priced({
            "draft_listing": {"price": 50.00},
            "pipeline_error": "price fetch failed: timeout",
        })
        assert r.result is FingerprintResult.CONTRADICTORY
        assert "pipeline_error" in r.reasons[0]

    def test_unknown_none(self):
        r = assert_priced(None)
        assert r.result is FingerprintResult.UNKNOWN

    def test_unknown_no_draft_price(self):
        r = assert_priced({"sku": "X"})
        assert r.result is FingerprintResult.UNKNOWN

    def test_not_applicable_unlisted(self):
        r = assert_priced({"listing_status": "not_listed"})
        assert r.result is FingerprintResult.NOT_APPLICABLE


class TestAssertStaged:
    def test_true(self):
        r = assert_staged({"ebay_offer": {"offer_id": "5012345678"}})
        assert r.result is FingerprintResult.TRUE

    def test_false_no_offer_id(self):
        r = assert_staged({"ebay_offer": {"status": "PENDING"}})
        assert r.result is FingerprintResult.FALSE

    def test_false_empty_offer_id(self):
        r = assert_staged({"ebay_offer": {"offer_id": ""}})
        assert r.result is FingerprintResult.FALSE

    def test_unknown_no_ebay_offer(self):
        r = assert_staged({"sku": "X"})
        assert r.result is FingerprintResult.UNKNOWN

    def test_unknown_none(self):
        r = assert_staged(None)
        assert r.result is FingerprintResult.UNKNOWN

    def test_not_applicable_unlisted(self):
        r = assert_staged({"listing_status": "not_listed"})
        assert r.result is FingerprintResult.NOT_APPLICABLE


class TestAssertPublished:
    def test_true_active(self):
        r = assert_published({"ebay_listing": {"status": "Active"}})
        assert r.result is FingerprintResult.TRUE

    def test_false_ended(self):
        r = assert_published({"ebay_listing": {"status": "Ended"}})
        assert r.result is FingerprintResult.FALSE
        assert "Ended" in r.reasons[0]

    def test_unknown_no_listing(self):
        r = assert_published({"sku": "X"})
        assert r.result is FingerprintResult.UNKNOWN

    def test_unknown_none(self):
        r = assert_published(None)
        assert r.result is FingerprintResult.UNKNOWN

    def test_not_applicable_unlisted(self):
        r = assert_published({"listing_status": "not_listed"})
        assert r.result is FingerprintResult.NOT_APPLICABLE


# ===================================================================
# Cross-cutting: all 6 FingerprintResult values exercised
# ===================================================================

def test_all_six_fingerprint_results_exercised():
    """Prove every FingerprintResult value is reachable through evidence sources."""
    results_seen = set()

    # TRUE
    results_seen.add(assert_tested(0, "pass").result)
    results_seen.add(assert_priced({"draft_listing": {"price": 10.0}}).result)

    # FALSE
    results_seen.add(assert_tested(1, "fail").result)
    results_seen.add(assert_priced({"draft_listing": {"price": 0}}).result)

    # UNKNOWN
    results_seen.add(assert_tested(None, "").result)
    results_seen.add(assert_priced(None).result)

    # STALE
    results_seen.add(assert_priced({
        "draft_listing": {"price": 100.00},
        "staged_price": 95.00,
    }).result)

    # CONTRADICTORY
    results_seen.add(assert_priced({
        "draft_listing": {"price": 50.00},
        "pipeline_error": "price fetch failed: timeout",
    }).result)

    # NOT_APPLICABLE
    results_seen.add(assert_item_has_photos({"listing_status": "not_listed"}).result)

    assert results_seen == {
        FingerprintResult.TRUE,
        FingerprintResult.FALSE,
        FingerprintResult.UNKNOWN,
        FingerprintResult.STALE,
        FingerprintResult.CONTRADICTORY,
        FingerprintResult.NOT_APPLICABLE,
    }


def test_evidence_assertions_are_immutable_and_hashable():
    """EvidenceAssertion must be usable in sets and as dict keys."""
    a1 = assert_tested(0, "ok")
    a2 = assert_tested(0, "ok")
    # same logical value
    assert a1 == a2
    # hashable
    s = {a1, a2}
    assert len(s) == 1


def test_generic_assert_condition_rejects_bad_type():
    with pytest.raises(TypeError, match="FingerprintResult"):
        assert_condition("x", "not_a_fingerprint")  # type: ignore[arg-type]


def test_priced_not_applicable_via_status_key():
    """NOT_APPLICABLE also triggered via 'status' key (not just listing_status)."""
    r = assert_priced({"status": "archived"})
    assert r.result is FingerprintResult.NOT_APPLICABLE
