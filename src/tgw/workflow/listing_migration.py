"""Observation-only decisions for bounded listing-pipeline migration seams."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .contracts import RuntimeWorkGraph, TreatmentDisposition
from .evaluator import evaluate
from .item_snapshot import build_item_snapshot
from .profiles import TGW_EBAY_IDENTIFIED
from .treatments import AI_IDENTIFY

EVALUATOR_VERSION = "pp-workflow-phase3-bundle/v1"

# Frozen Phase 3 inventory. ``migrate`` entries choose pipeline movement;
# ``retained-derived`` entries invalidate rebuildable projections;
# ``entrypoint-authority`` entries begin from an explicit operator request;
# ``scheduler-timer`` entries are recurring timing, not condition successors.
PHASE3_SUCCESSOR_INVENTORY: tuple[tuple[str, str, str, str], ...] = (
    ("src/tgw/workers/bundle_intake.py", "_enqueue_downstream", "catalog_rebuild", "retained-derived"),
    ("src/tgw/workers/bundle_intake.py", "_enqueue_downstream", "thumbnail_gen", "retained-derived"),
    ("src/tgw/workers/bundle_intake.py", "_enqueue_downstream", "ai_identify", "migrate"),
    ("src/tgw/workers/ebay_upload.py", "handle", "ebay_upload", "migrate"),
    ("src/tgw/workers/ebay_publish.py", "handle", "ebay_stage", "migrate"),
    ("src/tgw/workers/ebay_publish.py", "handle", "catalog_rebuild", "retained-derived"),
    ("src/tgw/workers/ebay_publish.py", "handle", "ebay_sync", "migrate"),
    ("src/tgw/workers/ebay_stage.py", "handle", "ebay_sync", "migrate"),
    ("src/tgw/workers/ebay_price.py", "handle", "catalog_rebuild", "retained-derived"),
    ("src/tgw/ebay/sync.py", "enqueue_post_push_sync", "ebay_sync", "migrate"),
    ("src/tgw/workers/ebay_dole.py", "handle", "ebay_publish", "migrate"),
    ("src/tgw/workers/ebay_dole.py", "run", "ebay_dole", "scheduler-timer"),
    ("src/tgw/workers/ebay_dole.py", "_reschedule", "ebay_dole", "scheduler-timer"),
    ("src/tgw/workers/ebay_sync.py", "run", "ebay_sync", "scheduler-timer"),
    ("src/tgw/workers/ebay_sync.py", "_reschedule", "ebay_sync", "scheduler-timer"),
    ("src/tgw/api.py", "cmd_hint", "ai_identify", "entrypoint-authority"),
    ("src/tgw/api.py", "cmd_resolve_legacy", "ebay_stage", "entrypoint-authority"),
    ("src/tgw/api.py", "cmd_publish", "ebay_publish", "entrypoint-authority"),
    ("src/tgw/http_server.py", "_enqueue_thumbnail_gen", "thumbnail_gen", "retained-derived"),
    ("src/tgw/http_server.py", "patch_item", "ebay_stage", "entrypoint-authority"),
    ("src/tgw/http_server.py", "_maybe_early_identify", "ai_identify", "entrypoint-authority"),
    ("src/tgw/http_server.py", "_maybe_session_complete_identify", "ai_identify", "entrypoint-authority"),
    ("src/tgw/http_server.py", "bulk_action", "ebay_upload", "entrypoint-authority"),
    ("src/tgw/http_server.py", "bulk_action", "ebay_stage", "entrypoint-authority"),
    ("src/tgw/http_server.py", "apply_revision", "ebay_sync", "entrypoint-authority"),
    ("src/tgw/http_server.py", "item_action", "ai_identify", "entrypoint-authority"),
    ("src/tgw/http_server.py", "item_action", "ebay_price", "entrypoint-authority"),
    ("src/tgw/http_server.py", "item_action", "ebay_upload", "entrypoint-authority"),
    ("src/tgw/http_server.py", "item_action", "ebay_stage", "entrypoint-authority"),
    ("src/tgw/http_server.py", "item_action", "ebay_publish", "entrypoint-authority"),
    ("src/tgw/http_server.py", "item_action", "ebay_sync", "entrypoint-authority"),
)

PHASE3_EXPLICIT_EXCLUSIONS: tuple[tuple[str, str, str, str], ...] = (
    ("src/tgw/workers/bundle_intake.py", "_enqueue", "bundle_intake", "intake job creation, not a successor"),
    ("src/tgw/workers/bundle_intake.py", "scan_newitems", "multi_intake", "multi-intake ingress handoff"),
    ("src/tgw/api.py", "cmd_enqueue_sku", "<dynamic>", "explicit manual treatment request"),
    ("src/tgw/api.py", "main", "ebay_publish", "CLI wrapper invokes explicit cmd_publish request"),
)


@dataclass(frozen=True)
class BundleDownstreamDecision:
    graph: RuntimeWorkGraph
    disposition: TreatmentDisposition | None

    @property
    def object_id(self) -> str:
        return self.graph.object_id

    @property
    def object_generation(self) -> str:
        return self.graph.object_generation

    @property
    def graph_id(self) -> str:
        return self.graph.graph_id

    @property
    def enqueue_ai_identify(self) -> bool:
        return self.disposition is not None


def derive_bundle_downstream(item_path: str | Path) -> BundleDownstreamDecision:
    """Derive only the legacy bundle→AI seam from authoritative ItemData.

    This function is pure/read-only and cannot dispatch provider work.  Catalog
    and thumbnail invalidations intentionally remain outside this decision;
    Phase 3 retains those existing derived-projection effects for parity.
    """
    snapshot = build_item_snapshot(
        item_path,
        TGW_EBAY_IDENTIFIED,
        treatments=(AI_IDENTIFY,),
    )
    graph = evaluate(
        snapshot=snapshot,
        goal=TGW_EBAY_IDENTIFIED,
        treatments=(AI_IDENTIFY,),
        evaluator_version=EVALUATOR_VERSION,
    )
    disposition = next(
        (
            item for item in graph.eligible_treatments
            if (item.treatment_id, item.treatment_version)
            == (AI_IDENTIFY.identity, AI_IDENTIFY.version)
        ),
        None,
    )
    return BundleDownstreamDecision(
        graph=graph,
        disposition=disposition,
    )
