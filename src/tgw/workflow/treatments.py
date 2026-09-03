"""Treatment contracts for TGW business/listing worker domains.

Each treatment is an immutable contract that declares:
- which fingerprint conditions must be satisfied before it can execute
- which conditions it may establish when it completes
- which ownership domains it claims (used for conflict detection)
- whether its effect class is LOCAL or EXTERNAL
"""

from __future__ import annotations

from tgw.workflow_kernel.contracts import EffectClass, FingerprintResult, Requirement, TreatmentContract

# ── Shared receipt schema ──────────────────────────────────────────────────
_RECEIPT = "receipt/tgw-workflow/v1"



NORMALIZE_CONDITION = TreatmentContract(
    identity="normalize-condition",
    version="1",
    requires=(
        Requirement("valid_condition", (FingerprintResult.FALSE,)),
        Requirement("condition_normalizable", (FingerprintResult.TRUE,)),
    ),
    may_establish=("valid_condition",),
    must_preserve=("photos", "draft_listing", "provider_state"),
    ownership=("item.condition",),
    effect_class=EffectClass.LOCAL,
    receipt_schema_id=_RECEIPT,
)

# ═══════════════════════════════════════════════════════════════════════════
#  TGW treatments
# ═══════════════════════════════════════════════════════════════════════════

AI_IDENTIFY = TreatmentContract(
    identity="ai-identify",
    version="1",
    requires=(Requirement("item_has_photos", (FingerprintResult.TRUE,)),),
    may_establish=("ai_identified",),
    must_preserve=("item_data",),
    ownership=("item.identity",),
    effect_class=EffectClass.LOCAL,
    receipt_schema_id=_RECEIPT,
)

EBAY_DRAFT = TreatmentContract(
    identity="ebay-draft",
    version="1",
    requires=(
        Requirement("inventory_available", (FingerprintResult.TRUE,)),
        Requirement("ai_identified", (FingerprintResult.TRUE,)),
    ),
    may_establish=("draft_generated",),
    must_preserve=("item_data",),
    ownership=("listing.draft",),
    effect_class=EffectClass.LOCAL,
    receipt_schema_id=_RECEIPT,
)

EBAY_PRICE = TreatmentContract(
    identity="ebay-price",
    version="1",
    requires=(
        Requirement("inventory_available", (FingerprintResult.TRUE,)),
        Requirement("draft_generated", (FingerprintResult.TRUE,)),
    ),
    may_establish=("priced",),
    must_preserve=("item_data",),
    ownership=("listing.price",),
    effect_class=EffectClass.LOCAL,
    receipt_schema_id=_RECEIPT,
)

EBAY_UPLOAD = TreatmentContract(
    identity="ebay-upload",
    version="1",
    requires=(
        Requirement("inventory_available", (FingerprintResult.TRUE,)),
        Requirement("item_has_photos", (FingerprintResult.TRUE,)),
        Requirement("ai_identified", (FingerprintResult.TRUE,)),
        Requirement("draft_generated", (FingerprintResult.TRUE,)),
        Requirement("priced", (FingerprintResult.TRUE,)),
        Requirement("operator_authorized_upload", (FingerprintResult.TRUE,)),
    ),
    may_establish=("photos_uploaded",),
    must_preserve=("item_data",),
    ownership=("listing.photos",),
    effect_class=EffectClass.EXTERNAL,
    receipt_schema_id=_RECEIPT,
)

EBAY_STAGE = TreatmentContract(
    identity="ebay-stage",
    version="1",
    requires=(
        Requirement("inventory_available", (FingerprintResult.TRUE,)),
        Requirement("draft_generated", (FingerprintResult.TRUE,)),
        Requirement("priced", (FingerprintResult.TRUE,)),
        Requirement("photos_uploaded", (FingerprintResult.TRUE,)),
        Requirement("operator_authorized_stage", (FingerprintResult.TRUE,)),
    ),
    may_establish=("staged", "staged_content_current"),
    must_preserve=("item_data",),
    ownership=("listing.stage",),
    effect_class=EffectClass.EXTERNAL,
    receipt_schema_id=_RECEIPT,
)

EBAY_PUBLISH = TreatmentContract(
    identity="ebay-publish",
    version="1",
    requires=(
        Requirement("inventory_available", (FingerprintResult.TRUE,)),
        Requirement("staged", (FingerprintResult.TRUE,)),
        Requirement("staged_content_current", (FingerprintResult.TRUE,)),
        Requirement("operator_authorized_publish", (FingerprintResult.TRUE,)),
    ),
    may_establish=("published",),
    must_preserve=("item_data",),
    ownership=("listing.publish",),
    effect_class=EffectClass.EXTERNAL,
    receipt_schema_id=_RECEIPT,
)

EBAY_SYNC_TARGETED = TreatmentContract(
    identity="ebay-sync-targeted",
    version="1",
    requires=(Requirement("provider_effect_succeeded", (FingerprintResult.TRUE,)),),
    may_establish=("provider_projection_current",),
    must_preserve=("provider_state",),
    ownership=("listing.provider_projection",),
    effect_class=EffectClass.EXTERNAL,
    receipt_schema_id=_RECEIPT,
)

EBAY_WITHDRAW = TreatmentContract(
    identity="ebay-withdraw",
    version="1",
    requires=(
        Requirement("published", (FingerprintResult.TRUE,)),
        Requirement("operator_authorized_withdraw", (FingerprintResult.TRUE,)),
    ),
    may_establish=("listing_inactive",),
    must_preserve=("item_data", "listing.provider_projection"),
    ownership=("listing.withdraw",),
    effect_class=EffectClass.EXTERNAL,
    receipt_schema_id=_RECEIPT,
)

EBAY_ONBOARD_LEGACY_STAGE = TreatmentContract(
    identity="ebay-onboard-legacy-stage",
    version="1",
    requires=(
        Requirement("staged", (FingerprintResult.TRUE,)),
        Requirement(
            "staged_content_current",
            (FingerprintResult.UNKNOWN, FingerprintResult.STALE),
        ),
    ),
    may_establish=("staged_content_current",),
    must_preserve=("provider_state",),
    ownership=("listing.legacy_stage_evidence",),
    effect_class=EffectClass.LOCAL,
    receipt_schema_id=_RECEIPT,
)


# ── Grouped access ─────────────────────────────────────────────────────────

TGW_TREATMENTS: tuple[TreatmentContract, ...] = (
    NORMALIZE_CONDITION,
    AI_IDENTIFY,
    EBAY_DRAFT,
    EBAY_PRICE,
    EBAY_UPLOAD,
    EBAY_STAGE,
    EBAY_PUBLISH,
    EBAY_SYNC_TARGETED,
)

# Dormant until an explicit producer/admission wave.  Keeping this contract out
# of TGW_TREATMENTS prevents the ordinary evaluator from auto-dispatching it.
LEGACY_STAGE_ONBOARDING_TREATMENTS: tuple[TreatmentContract, ...] = (
    EBAY_ONBOARD_LEGACY_STAGE,
)

# Operator-command withdrawal: dispatched only through the explicit
# listing-withdraw migration path (never by the ordinary evaluator, which
# reads TGW_TREATMENTS).  Group kept separate per the branch stream; coding
# treatments were split out to tgw.development.treatments on main and are NOT
# re-declared here (no duplicate treatment ids).
WITHDRAW_TREATMENTS: tuple[TreatmentContract, ...] = (EBAY_WITHDRAW,)

ALL_TREATMENTS: tuple[TreatmentContract, ...] = TGW_TREATMENTS + WITHDRAW_TREATMENTS
