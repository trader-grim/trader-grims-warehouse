"""Treatment dispatch scheduler — enqueues workers based on evaluator output.

Phase 3 spine: receives TreatmentDisposition from evaluate(), maps to
queue worker enqueue calls. Phase 4: receives receipts from workers,
re-evaluates the item to pick the next eligible treatment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .contracts import TreatmentDisposition

log = logging.getLogger(__name__)


# Mapping from treatment_id to queue worker name for enqueue
_TREATMENT_QUEUE_MAP: dict[str, str] = {
    "ai-identify": "ai_identify",
    "ebay-draft": "ebay_draft",
    "ebay-price": "ebay_price",
    "ebay-upload": "ebay_upload",
    "ebay-stage": "ebay_stage",
    "ebay-publish": "ebay_publish",
    "alt-text": "alt_text",
    "catalog-rebuild": "catalog_rebuild",
}


@dataclass(frozen=True)
class DispatchResult:
    """Result of dispatching a single treatment."""

    treatment_id: str
    treatment_version: str
    queue_name: str
    entity_id: str
    enqueued: bool
    job_id: str = ""


def _treatment_to_queue(treatment_id: str) -> str:
    """Resolve treatment_id to queue worker name."""
    return _TREATMENT_QUEUE_MAP.get(treatment_id, treatment_id)


def dispatch_treatment(
    *,
    disposition: TreatmentDisposition,
    entity_id: str,
    payload_extra: dict[str, Any] | None = None,
    enqueue_fn: Any = None,
) -> DispatchResult:
    """Dispatch one eligible treatment by enqueuing its worker.

    In production, enqueue_fn is state_machine.enqueue_job. For tests, pass a
    callable with the same signature to avoid DB dependencies.

    Returns a DispatchResult describing what was enqueued.
    """
    queue_name = _treatment_to_queue(disposition.treatment_id)

    if enqueue_fn is None:
        # Lazily import to avoid DB dependency in pure tests
        from tgw.queue.state_machine import enqueue_job

        enqueue_fn = enqueue_job

    payload = {"entity_id": entity_id}
    if payload_extra:
        payload.update(payload_extra)

    try:
        job_id = enqueue_fn(
            queue_name=queue_name,
            payload=payload,
            entity_type="item",
            entity_id=entity_id,
            dedupe_key=f"{queue_name}:{entity_id}",
            max_attempts=3,
        )
        log.info(
            "dispatched %s → %s (job %s)",
            disposition.treatment_id,
            queue_name,
            job_id,
        )
        return DispatchResult(
            treatment_id=disposition.treatment_id,
            treatment_version=disposition.treatment_version,
            queue_name=queue_name,
            entity_id=entity_id,
            enqueued=True,
            job_id=job_id,
        )
    except Exception:
        log.exception(
            "failed to dispatch %s → %s",
            disposition.treatment_id,
            queue_name,
        )
        return DispatchResult(
            treatment_id=disposition.treatment_id,
            treatment_version=disposition.treatment_version,
            queue_name=queue_name,
            entity_id=entity_id,
            enqueued=False,
        )
