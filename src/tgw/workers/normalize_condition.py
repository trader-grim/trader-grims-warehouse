"""Generation-bound local treatment for canonicalizing ItemData condition."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable, Mapping

from tgw.config import DEFAULT_CONFIG, load_config
from tgw.errors import TreatmentFailure
from tgw.queue.worker_base import QueueWorker
from tgw.workflow.condition_normalization import normalized_condition

TREATMENT_ID, TREATMENT_VERSION, QUEUE_NAME = "normalize-condition", "1", "normalize_condition"
MutationFn = Callable[..., Any]


def _receipt(graph_id: str | None, outcome: str, *, detail: str = "", **evidence: Any) -> dict[str, Any]:
    return {"treatment_id": TREATMENT_ID, "treatment_version": TREATMENT_VERSION,
            "graph_id": graph_id, "outcome": outcome,
            "established_conditions": ["valid_condition"] if outcome == "satisfied" else [],
            "artifacts": ([f"item-mutation:{evidence['operation_id']}"]
                          if outcome == "satisfied" else []),
            "error_detail": detail, "evidence": evidence,
            "receipt_schema_id": "treatment-receipt/v1"}


def _text(source: Mapping[str, Any], key: str) -> str | None:
    value = source.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _status(result: Any) -> str:
    if isinstance(result, Mapping):
        value = result.get("status") or result.get("state") or result.get("outcome")
    else:
        value = next((getattr(result, key, None) for key in ("status", "state", "outcome")
                      if getattr(result, key, None) is not None), None)
    return str(getattr(value, "value", value) or "").upper()


def apply_condition_mutation(
    *,
    config: Mapping[str, Any],
    sku: str,
    job_id: str,
    graph_id: str,
    expected_generation: str,
) -> Any:
    """Adapt this treatment to the generic durable item-mutation boundary."""
    from tgw.config import sku_json
    from tgw.item_mutation import (
        mutate_item,
        operation_identity,
        resolve_item_mutation_journal_root,
    )
    from tgw.sqlite_catalog import upsert_catalog_row

    mutation_payload = {"graph_id": graph_id, "request_id": job_id}
    operation_id = operation_identity(
        sku=sku,
        kind=TREATMENT_ID,
        expected_generation=expected_generation,
        payload=mutation_payload,
    )

    def mutate(document: dict[str, Any]) -> dict[str, Any]:
        if document.get("sku") != sku:
            raise ValueError("authoritative document SKU does not match requested SKU")
        condition = normalized_condition(document.get("condition"))
        if condition is None:
            raise ValueError("authoritative condition is not an explicitly supported alias")
        updated = dict(document)
        updated["condition"] = condition
        return updated

    def project(_sku: str, document: dict[str, Any]) -> None:
        result = upsert_catalog_row(dict(config), document)
        if not isinstance(result, Mapping) or result.get("ok") is not True:
            raise RuntimeError("SQLite projection did not report success")

    data_root = Path(config.get("data_root", "/opt/TGW/data"))
    journal_root = resolve_item_mutation_journal_root(config)
    return mutate_item(
        item_path=sku_json(dict(config), sku),
        archive_root=Path(config.get("archive_root", data_root / "ItemArchive")),
        journal_root=journal_root,
        sku=sku,
        kind=TREATMENT_ID,
        expected_generation=expected_generation,
        payload=mutation_payload,
        mutate=mutate,
        project=project,
        operation_id=operation_id,
    )


def handle_job(job: Mapping[str, Any], config: Mapping[str, Any], *, mutation_fn: MutationFn) -> dict[str, Any]:
    payload = job.get("payload_json")
    if not isinstance(payload, Mapping):
        return _receipt(None, "failed", detail="payload_json must be an object",
                        reason_code="INVALID_PAYLOAD")
    graph_id, job_id = _text(payload, "graph_id"), _text(job, "job_id")
    sku = _text(payload, "sku") or _text(payload, "entity_id")
    entity_id = _text(payload, "entity_id") or _text(job, "entity_id")
    generation = _text(payload, "object_generation")
    treatment_id, version = _text(payload, "treatment_id"), _text(payload, "treatment_version")
    required = {"job_id": job_id, "sku/entity_id": sku, "entity_id": entity_id,
                "object_generation": generation, "graph_id": graph_id,
                "treatment_id": treatment_id, "treatment_version": version}
    missing = sorted(key for key, value in required.items() if value is None)
    if missing:
        return _receipt(graph_id, "failed", detail=f"missing required identity fields: {', '.join(missing)}",
                        reason_code="INVALID_IDENTITY", missing=missing)
    if treatment_id != TREATMENT_ID or version != TREATMENT_VERSION:
        return _receipt(graph_id, "failed", detail="treatment identity/version mismatch",
                        reason_code="TREATMENT_MISMATCH")
    if _text(payload, "sku") and sku != entity_id:
        return _receipt(graph_id, "failed", detail="sku and entity_id mismatch",
                        reason_code="ENTITY_MISMATCH")
    try:
        result = mutation_fn(config=config, sku=sku, job_id=job_id, graph_id=graph_id,
                             expected_generation=generation)
    except Exception as exc:
        return _receipt(graph_id, "failed", detail="item mutation did not commit",
                        reason_code="MUTATION_ERROR", error_type=type(exc).__name__)
    status = _status(result)
    operation_id = str(getattr(result, "operation_id", "") or
                       (result.get("operation_id", "") if isinstance(result, Mapping) else ""))
    changed = (result.get("changed") if isinstance(result, Mapping)
               else getattr(result, "changed", None))
    resulting_generation = (
        result.get("resulting_generation") if isinstance(result, Mapping)
        else getattr(result, "resulting_generation", None)
    )
    evidence = {"operation_id": operation_id, "mutation_status": status,
                "object_generation": generation,
                "changed": changed,
                "resulting_generation": resulting_generation}
    if status != "COMMITTED":
        return _receipt(graph_id, "failed", detail="item mutation did not report COMMITTED",
                        reason_code="MUTATION_NOT_COMMITTED", **evidence)
    return _receipt(graph_id, "satisfied", **evidence)


class NormalizeConditionWorker(QueueWorker):
    def handle(self, job: dict[str, Any]) -> dict[str, Any]:
        receipt = handle_job(job, self.config, mutation_fn=apply_condition_mutation)
        payload = job.get("payload_json") or {}
        receipt.update({
            key: payload.get(key) for key in (
                "goal_profile_id", "goal_profile_version",
                "object_generation", "condition_hash",
            )
        })
        receipt["entity_id"] = job.get("entity_id")
        if receipt["outcome"] != "satisfied":
            reason = receipt.get("evidence", {}).get("reason_code", "TREATMENT_FAILED")
            raise TreatmentFailure(f"{TREATMENT_ID} did not commit: {reason}", receipt)
        return receipt


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-normalize-condition-worker")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--queue", default=QUEUE_NAME)
    args = parser.parse_args()
    NormalizeConditionWorker(args.queue, load_config(Path(args.config))).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
