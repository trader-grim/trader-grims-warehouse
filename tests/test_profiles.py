"""Tests for tgw.workflow.profiles — pure declarative verification."""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from tgw.workflow.contracts import GoalProfile  # noqa: E402
from tgw.workflow.profiles import (  # noqa: E402
    CODING_DEPLOYED,
    CODING_READY_FOR_ADMISSION,
    CODING_READY_FOR_IMPLEMENTATION,
    CODING_READY_FOR_REVIEW,
    PROFILE,
    PROFILE_META,
    TGW_EBAY_DRAFTED,
    TGW_EBAY_IDENTIFIED,
    TGW_EBAY_LISTABLE,
    TGW_EBAY_PRICED,
    TGW_EBAY_STAGED,
    ProfileMeta,
    all_profiles,
    coding_profiles,
    get_meta,
    get_profile,
    tgw_profiles,
)

# ======================================================================
# Coding profiles
# ======================================================================


class TestCodingReadyForImplementation:
    def test_identity(self):
        assert CODING_READY_FOR_IMPLEMENTATION.identity == "coding.ready_for_implementation"

    def test_version(self):
        assert CODING_READY_FOR_IMPLEMENTATION.version == "1"

    def test_required(self):
        assert CODING_READY_FOR_IMPLEMENTATION.required == (
            "implemented", "tested", "linted",
        )


class TestCodingReadyForReview:
    def test_identity(self):
        assert CODING_READY_FOR_REVIEW.identity == "coding.ready_for_review"

    def test_required(self):
        assert CODING_READY_FOR_REVIEW.required == (
            "implemented", "tested", "linted",
        )


class TestCodingReadyForAdmission:
    def test_identity(self):
        assert CODING_READY_FOR_ADMISSION.identity == "coding.ready_for_admission"

    def test_required(self):
        assert CODING_READY_FOR_ADMISSION.required == (
            "implemented", "tested", "linted", "reviewed", "controller_verified",
        )


class TestCodingDeployed:
    def test_identity(self):
        assert CODING_DEPLOYED.identity == "coding.deployed"

    def test_required(self):
        assert CODING_DEPLOYED.required == (
            "implemented", "tested", "linted", "reviewed", "controller_verified",
            "admitted", "committed", "deployed",
        )


class TestCodingCumulative:
    """Each successive coding profile is a superset of the previous."""

    def test_review_equals_implementation(self):
        assert set(CODING_READY_FOR_IMPLEMENTATION.required) == set(
            CODING_READY_FOR_REVIEW.required,
        )

    def test_admission_superset_of_review(self):
        assert set(CODING_READY_FOR_REVIEW.required) < set(
            CODING_READY_FOR_ADMISSION.required,
        )

    def test_deployed_superset_of_admission(self):
        assert set(CODING_READY_FOR_ADMISSION.required) < set(
            CODING_DEPLOYED.required,
        )


# ======================================================================
# TGW profiles
# ======================================================================


class TestTgwEbayIdentified:
    def test_identity(self):
        assert TGW_EBAY_IDENTIFIED.identity == "tgw.ebay_identified"

    def test_required(self):
        assert TGW_EBAY_IDENTIFIED.required == ("item_has_photos", "ai_identified")


class TestTgwEbayDrafted:
    def test_required(self):
        assert TGW_EBAY_DRAFTED.required == (
            "item_has_photos", "ai_identified", "draft_generated",
        )


class TestTgwEbayPriced:
    def test_required(self):
        assert TGW_EBAY_PRICED.required == (
            "item_has_photos", "ai_identified", "draft_generated", "priced",
        )


class TestTgwEbayStaged:
    def test_required(self):
        assert TGW_EBAY_STAGED.required == (
            "item_has_photos", "ai_identified", "draft_generated", "priced",
            "photos_uploaded", "staged", "staged_content_current",
        )


class TestTgwEbayListable:
    def test_required(self):
        assert TGW_EBAY_LISTABLE.required == (
            "item_has_photos", "ai_identified", "draft_generated", "priced",
            "photos_uploaded", "staged", "staged_content_current",
            "valid_condition", "valid_category",
            "title_ok", "published",
        )

    def test_published_is_final_condition(self):
        assert TGW_EBAY_LISTABLE.required[-1] == "published"


class TestTgwCumulative:
    """Each successive TGW profile is a strict superset of the previous."""

    def test_drafted_superset_of_identified(self):
        assert set(TGW_EBAY_IDENTIFIED.required) < set(TGW_EBAY_DRAFTED.required)

    def test_priced_superset_of_drafted(self):
        assert set(TGW_EBAY_DRAFTED.required) < set(TGW_EBAY_PRICED.required)

    def test_staged_superset_of_priced(self):
        assert set(TGW_EBAY_PRICED.required) < set(TGW_EBAY_STAGED.required)

    def test_listable_superset_of_staged(self):
        assert set(TGW_EBAY_STAGED.required) < set(TGW_EBAY_LISTABLE.required)


# ======================================================================
# Registry
# ======================================================================


class TestProfileRegistry:
    def test_dict_has_nine_entries(self):
        assert len(PROFILE) == 9

    def test_all_values_are_goal_profiles(self):
        for p in PROFILE.values():
            assert isinstance(p, GoalProfile)

    def test_identities_are_unique(self):
        ids = [p.identity for p in PROFILE.values()]
        assert len(ids) == len(set(ids))

    def test_keys_match_identities(self):
        for key, profile in PROFILE.items():
            assert key == profile.identity


class TestGetProfile:
    def test_returns_correct_profile(self):
        p = get_profile("coding.ready_for_implementation")
        assert p is CODING_READY_FOR_IMPLEMENTATION

    def test_raises_keyerror_for_unknown_identity(self):
        try:
            get_profile("nonexistent.unknown")
            raise AssertionError("Expected KeyError")
        except KeyError:
            pass


class TestAllProfiles:
    def test_returns_nine(self):
        assert len(all_profiles()) == 9

    def test_returns_tuples(self):
        assert isinstance(all_profiles(), tuple)


class TestCodingProfilesHelper:
    def test_returns_four(self):
        profiles = coding_profiles()
        assert len(profiles) == 4

    def test_all_have_coding_prefix(self):
        for p in coding_profiles():
            assert p.identity.startswith("coding.")

    def test_exact_identities(self):
        ids = {p.identity for p in coding_profiles()}
        assert ids == {
            "coding.ready_for_implementation",
            "coding.ready_for_review",
            "coding.ready_for_admission",
            "coding.deployed",
        }


class TestTgwProfilesHelper:
    def test_returns_five(self):
        profiles = tgw_profiles()
        assert len(profiles) == 5

    def test_all_have_tgw_prefix(self):
        for p in tgw_profiles():
            assert p.identity.startswith("tgw.")

    def test_exact_identities(self):
        ids = {p.identity for p in tgw_profiles()}
        assert ids == {
            "tgw.ebay_identified",
            "tgw.ebay_drafted",
            "tgw.ebay_priced",
            "tgw.ebay_staged",
            "tgw.ebay_listable",
        }


# ======================================================================
# Profile metadata
# ======================================================================


class TestProfileMetaRegistry:
    def test_every_profile_has_meta(self):
        for p in all_profiles():
            meta = get_meta(p.identity)
            assert isinstance(meta, ProfileMeta)
            assert meta.description
            assert meta.evidence_source_class

    def test_meta_keys_match_profile_identities(self):
        assert set(PROFILE_META.keys()) == set(PROFILE.keys())


class TestCodingMeta:
    def test_implementation_meta(self):
        meta = get_meta("coding.ready_for_implementation")
        assert meta.evidence_source_class == "ci_pipeline"
        assert meta.accepted_results == ()

    def test_admission_meta(self):
        meta = get_meta("coding.ready_for_admission")
        assert meta.evidence_source_class == "review_system"

    def test_deployed_meta(self):
        meta = get_meta("coding.deployed")
        assert meta.evidence_source_class == "deployment_system"


class TestTgwMeta:
    def test_identified_meta(self):
        meta = get_meta("tgw.ebay_identified")
        assert meta.evidence_source_class == "ai_identification"

    def test_drafted_meta(self):
        meta = get_meta("tgw.ebay_drafted")
        assert meta.evidence_source_class == "draft_generation"

    def test_priced_meta(self):
        meta = get_meta("tgw.ebay_priced")
        assert meta.evidence_source_class == "pricing_engine"

    def test_staged_meta(self):
        meta = get_meta("tgw.ebay_staged")
        assert meta.evidence_source_class == "ebay_staging"

    def test_listable_meta(self):
        meta = get_meta("tgw.ebay_listable")
        assert meta.evidence_source_class == "ebay_listing_validation"

    def test_listable_published_accepted_results(self):
        meta = get_meta("tgw.ebay_listable")
        assert ("published", "TRUE") in meta.accepted_results
        assert ("published", "NOT_APPLICABLE") in meta.accepted_results
        assert len(meta.accepted_results) == 2


class TestProfileMetaFrozen:
    def test_cannot_mutate_description(self):
        meta = get_meta("coding.ready_for_implementation")
        try:
            meta.description = "changed"  # type: ignore[misc]
            raise AssertionError("Expected FrozenInstanceError")
        except FrozenInstanceError:
            pass

    def test_cannot_mutate_evidence_source_class(self):
        meta = get_meta("tgw.ebay_identified")
        try:
            meta.evidence_source_class = "changed"  # type: ignore[misc]
            raise AssertionError("Expected FrozenInstanceError")
        except FrozenInstanceError:
            pass


# ======================================================================
# Invariant: all profiles are frozen
# ======================================================================


class TestAllProfilesFrozen:
    def test_cannot_mutate_required(self):
        for p in all_profiles():
            try:
                p.required = ()  # type: ignore[misc]
                raise AssertionError(f"{p.identity} is not frozen")
            except (AttributeError, FrozenInstanceError):
                pass
