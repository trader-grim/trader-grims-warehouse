"""Goal profile registry for TGW business/listing domains.

Pure declarative definitions — no logic, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from tgw.workflow_kernel.contracts import GoalProfile


@dataclass(frozen=True)
class ProfileMeta:
    """Metadata for a goal profile.

    Attributes:
        description: Human-readable description of the profile.
        evidence_source_class: Class of evidence source used to evaluate this profile.
        accepted_results: Specific FingerprintResult expectations for individual
            conditions, stored as (condition_id, accepted_result) pairs.
    """

    description: str
    evidence_source_class: str
    accepted_results: tuple[tuple[str, str], ...] = ()


# ---------------------------------------------------------------------------
# TGW domain profiles
# ---------------------------------------------------------------------------

TGW_EBAY_IDENTIFIED = GoalProfile(
    identity="tgw.ebay_identified",
    version="1",
    required=("item_has_photos", "ai_identified"),
)

TGW_EBAY_DRAFTED = GoalProfile(
    identity="tgw.ebay_drafted",
    version="1",
    required=("inventory_available", "item_has_photos", "ai_identified", "draft_generated"),
)

TGW_EBAY_PRICED = GoalProfile(
    identity="tgw.ebay_priced",
    version="1",
    required=("inventory_available", "item_has_photos", "ai_identified", "draft_generated", "priced"),
)

TGW_EBAY_STAGED = GoalProfile(
    identity="tgw.ebay_staged",
    version="1",
    required=(
        "inventory_available", "item_has_photos", "ai_identified", "draft_generated", "priced",
        "photos_uploaded", "staged", "staged_content_current",
    ),
)

TGW_EBAY_LISTABLE = GoalProfile(
    identity="tgw.ebay_listable",
    version="1",
    required=(
        "inventory_available", "item_has_photos", "ai_identified", "draft_generated", "priced",
        "photos_uploaded", "staged", "staged_content_current",
        "valid_condition", "valid_category",
        "title_ok", "listing_provider_consistent", "published",
    ),
)

TGW_EBAY_RECONCILED = GoalProfile(
    identity="tgw.ebay_reconciled", version="1",
    required=("provider_projection_current",),
)

TGW_EBAY_LEGACY_STAGE_ONBOARDED = GoalProfile(
    identity="tgw.ebay_legacy_stage_onboarded", version="1",
    required=("staged", "staged_content_current"),
)

# Dormant and intentionally absent from PROFILE and all registry helpers.
LEGACY_STAGE_ONBOARDING_PROFILES: tuple[GoalProfile, ...] = (
    TGW_EBAY_LEGACY_STAGE_ONBOARDED,
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PROFILE: dict[str, GoalProfile] = {
    p.identity: p
    for p in (
        TGW_EBAY_IDENTIFIED,
        TGW_EBAY_DRAFTED,
        TGW_EBAY_PRICED,
        TGW_EBAY_STAGED,
        TGW_EBAY_LISTABLE,
        TGW_EBAY_RECONCILED,
    )
}

PROFILE_META: dict[str, ProfileMeta] = {
    "tgw.ebay_identified": ProfileMeta(
        description="Item has photos and has been AI-identified.",
        evidence_source_class="ai_identification",
    ),
    "tgw.ebay_drafted": ProfileMeta(
        description="Item has been identified and a draft listing has been generated.",
        evidence_source_class="draft_generation",
    ),
    "tgw.ebay_priced": ProfileMeta(
        description="Item has been identified, drafted, and priced.",
        evidence_source_class="pricing_engine",
    ),
    "tgw.ebay_staged": ProfileMeta(
        description=(
            "Item has been identified, drafted, priced, photos uploaded, "
            "and staged to eBay."
        ),
        evidence_source_class="ebay_staging",
    ),
    "tgw.ebay_listable": ProfileMeta(
        description=(
            "Item meets all eBay listing requirements including validation "
            "of condition, category, and title."
        ),
        evidence_source_class="ebay_listing_validation",
        accepted_results=(
            ("published", "TRUE"),
            ("published", "NOT_APPLICABLE"),
        ),
    ),
    "tgw.ebay_reconciled": ProfileMeta(
        description="Provider projection matches one exact successful source effect.",
        evidence_source_class="provider_effect_receipt",
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_profile(identity: str) -> GoalProfile:
    """Return the GoalProfile for *identity*.

    Raises KeyError if *identity* is not registered.
    """
    return PROFILE[identity]


def get_meta(identity: str) -> ProfileMeta:
    """Return the ProfileMeta for *identity*.

    Raises KeyError if *identity* is not registered.
    """
    return PROFILE_META[identity]


def all_profiles() -> tuple[GoalProfile, ...]:
    """Return every registered profile."""
    return tuple(PROFILE.values())




def tgw_profiles() -> tuple[GoalProfile, ...]:
    """Return all profiles in the ``tgw`` domain."""
    return tuple(
        p for p in PROFILE.values() if p.identity.startswith("tgw.")
    )
