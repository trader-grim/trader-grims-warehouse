"""Treatment contracts for coding and TGW worker domains.

Each treatment is an immutable contract that declares:
- which fingerprint conditions must be satisfied before it can execute
- which conditions it may establish when it completes
- which ownership domains it claims (used for conflict detection)
- whether its effect class is LOCAL or EXTERNAL
"""

from __future__ import annotations

from .contracts import EffectClass, FingerprintResult, Requirement, TreatmentContract

# ── Shared receipt schema ──────────────────────────────────────────────────
_RECEIPT = "receipt/tgw-workflow/v1"


# ═══════════════════════════════════════════════════════════════════════════
#  Coding treatments
# ═══════════════════════════════════════════════════════════════════════════

CODEX_IMPLEMENT = TreatmentContract(
    identity="codex-implement",
    version="1",
    requires=(Requirement("implemented", (FingerprintResult.FALSE,)),),
    may_establish=("implemented", "tested"),
    must_preserve=("source_files",),
    ownership=("code.implement",),
    effect_class=EffectClass.LOCAL,
    receipt_schema_id=_RECEIPT,
)

CLAUDE_REVIEW = TreatmentContract(
    identity="claude-review",
    version="1",
    requires=(
        Requirement("implemented", (FingerprintResult.TRUE,)),
        Requirement("tested", (FingerprintResult.TRUE,)),
        Requirement("linted", (FingerprintResult.TRUE,)),
    ),
    may_establish=("reviewed",),
    must_preserve=("source_files",),
    ownership=("code.review",),
    effect_class=EffectClass.LOCAL,
    receipt_schema_id=_RECEIPT,
)

CONTROLLER_VERIFY = TreatmentContract(
    identity="controller-verify",
    version="1",
    requires=(Requirement("implemented", (FingerprintResult.TRUE,)),),
    may_establish=("tested", "linted", "controller_verified"),
    must_preserve=("source_files",),
    ownership=("code.verify",),
    effect_class=EffectClass.LOCAL,
    receipt_schema_id=_RECEIPT,
)

HERMES_STITCH = TreatmentContract(
    identity="hermes-stitch",
    version="1",
    requires=(
        Requirement("reviewed", (FingerprintResult.TRUE,)),
        Requirement("controller_verified", (FingerprintResult.TRUE,)),
    ),
    # An approved Plan/PP/Todo authorizes the local execution sequence.  The
    # independent review and controller receipts are evidence gates, not
    # requests for another human admission.
    may_establish=("committed",),
    must_preserve=("source_files",),
    ownership=("code.stitch",),
    effect_class=EffectClass.LOCAL,
    receipt_schema_id=_RECEIPT,
)


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
    requires=(Requirement("ai_identified", (FingerprintResult.TRUE,)),),
    may_establish=("draft_generated",),
    must_preserve=("item_data",),
    ownership=("listing.draft",),
    effect_class=EffectClass.LOCAL,
    receipt_schema_id=_RECEIPT,
)

EBAY_PRICE = TreatmentContract(
    identity="ebay-price",
    version="1",
    requires=(Requirement("draft_generated", (FingerprintResult.TRUE,)),),
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
        Requirement("item_has_photos", (FingerprintResult.TRUE,)),
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


# ── Grouped access ─────────────────────────────────────────────────────────

CODING_TREATMENTS: tuple[TreatmentContract, ...] = (
    CODEX_IMPLEMENT,
    CLAUDE_REVIEW,
    CONTROLLER_VERIFY,
    HERMES_STITCH,
)

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

ALL_TREATMENTS: tuple[TreatmentContract, ...] = CODING_TREATMENTS + TGW_TREATMENTS
