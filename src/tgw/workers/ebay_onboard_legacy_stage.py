"""Dormant governed onboarding for corroborated legacy staged eBay offers.

The public worker entrypoint is intentionally fail-closed.  Only the private
governed handler is exercised by isolated tests until a separate admission.
"""
from __future__ import annotations

import argparse
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict

from tgw.config import DEFAULT_CONFIG, load_config
from tgw.errors import HardFailure, TreatmentFailure
from tgw.legacy_stage_corroboration import (
    build_and_record_legacy_stage_observation,
    compare_legacy_stage_observation,
    read_legacy_stage_observation,
)
from tgw.provider_observations import build_provider_observation, record_provider_observation
from tgw.queue import state_machine
from tgw.queue.worker_base import QueueWorker

TREATMENT_ID = "ebay-onboard-legacy-stage"
TREATMENT_VERSION = "1"
CHECKPOINT_SCHEMA = "legacy-stage-onboarding-checkpoint/v1"
PAYLOAD_SCHEMA = "ebay-onboard-legacy-stage/v1"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class EbayOnboardLegacyStageWorker(QueueWorker):
    def handle(self, job: Dict[str, Any]) -> Dict[str, Any] | None:
        payload = job.get("payload_json")
        if not isinstance(payload, dict) or payload.get("payload_schema_id") != PAYLOAD_SCHEMA:
            raise HardFailure("legacy onboarding payload schema is unsupported")
        migration = self.config.get("workflow_migration")
        if migration is None and isinstance(self.config.get("raw"), dict):
            migration = self.config["raw"].get("workflow_migration")
        mode = (migration or {}).get("ebay_legacy_stage_onboarding_consumer", "off")
        if mode not in {"off", "workflow"}:
            raise HardFailure("legacy onboarding consumer selector is invalid")
        if mode != "workflow":
            raise HardFailure("legacy staged-offer onboarding consumer is disabled")
        return self._handle_governed(job)

    def _receipt(self, payload, sku, outcome, reason, **evidence):
        return {
            "receipt_schema_id": "treatment-receipt/v1",
            "treatment_id": TREATMENT_ID,
            "treatment_version": TREATMENT_VERSION,
            "graph_id": payload["graph_id"],
            "goal_profile_id": payload["goal_profile_id"],
            "goal_profile_version": payload["goal_profile_version"],
            "object_generation": payload["object_generation"],
            "condition_hash": payload["condition_hash"],
            "entity_id": sku, "outcome": outcome,
            "established_conditions": (
                ["staged_content_current"]
                if outcome == "satisfied" else []
            ),
            "artifacts": [f"item:{sku}"],
            "evidence": {"reason_code": reason, **evidence},
        }

    def _validate_binding(self, payload, sku, job):
        required = (
            "sku", "offer_id", "provider_identity", "object_generation",
            "graph_id", "condition_hash", "content_identity",
            "goal_profile_id", "goal_profile_version", "treatment_id",
            "treatment_version",
        )
        missing = [key for key in required if not isinstance(payload.get(key), str)
                   or not payload[key].strip()]
        if missing:
            raise HardFailure("legacy onboarding missing binding: " + ", ".join(missing))
        if not _HEX64.fullmatch(payload["content_identity"]):
            raise HardFailure("legacy onboarding content identity is malformed")
        if (payload["treatment_id"], payload["treatment_version"]) != (
            TREATMENT_ID, TREATMENT_VERSION,
        ):
            raise HardFailure("legacy onboarding treatment binding mismatch")
        if (job.get("entity_type") != "item" or job.get("entity_id") != sku
                or payload["sku"] != sku or payload.get("entity_id") != sku):
            raise HardFailure("legacy onboarding entity binding mismatch")
        configured = self._provider_identity()
        if payload["provider_identity"] != configured:
            raise HardFailure("legacy onboarding provider identity mismatch")
        try:
            job_id = str(uuid.UUID(str(job.get("job_id"))))
            lease_token = str(uuid.UUID(str(job.get("lease_token"))))
        except (TypeError, ValueError, AttributeError) as exc:
            raise HardFailure("legacy onboarding lacks live lease identity") from exc
        if not isinstance(getattr(self, "owner", None), str) or not self.owner.strip():
            raise HardFailure("legacy onboarding lacks live lease identity")
        return job_id, lease_token

    def _provider_identity(self):
        migration = self.config.get("workflow_migration")
        if migration is None and isinstance(self.config.get("raw"), dict):
            migration = self.config["raw"].get("workflow_migration")
        value = migration.get("ebay_provider_identity") if isinstance(migration, dict) else None
        if not isinstance(value, str) or not value.strip():
            raise HardFailure("legacy onboarding provider identity is unconfigured")
        return value

    def _comparison(self, payload, offer, inventory, observed_provider_identity):
        return compare_legacy_stage_observation(
            sku=payload["sku"], offer_id=payload["offer_id"],
            trusted_provider_identity=payload["provider_identity"],
            observed_provider_identity=observed_provider_identity,
            object_generation=payload["object_generation"],
            graph_id=payload["graph_id"], condition_hash=payload["condition_hash"],
            content_identity=payload["content_identity"],
            expected_inventory=payload["expected_inventory_item"],
            expected_offer=payload["expected_offer"],
            observed_inventory=inventory, observed_offer=offer,
        )

    def _checkpoint(
        self, payload, comparison, offer, inventory, observed_at,
        observed_provider_identity,
    ):
        from tgw.item_mutation import operation_identity

        preview = build_provider_observation(
            provider="ebay", provider_identity=payload["provider_identity"],
            sku=payload["sku"], offer_id=payload["offer_id"],
            object_generation=payload["object_generation"],
            graph_id=payload["graph_id"], condition_hash=payload["condition_hash"],
            content_identity=payload["content_identity"], outcome=comparison.outcome,
            evidence={
                **comparison.evidence,
                "expected_request_fingerprint": comparison.expected_request_fingerprint,
            },
            observed_at=observed_at,
        )
        marker_payload = {
            "content_identity": payload["content_identity"],
            "comparison_fingerprint": comparison.comparison_fingerprint,
            "observation_id": preview.observation_id,
        }
        operation_id = operation_identity(
            sku=payload["sku"], kind=TREATMENT_ID,
            expected_generation=payload["object_generation"], payload=marker_payload,
        )
        return {
            "schema_id": CHECKPOINT_SCHEMA, "sku": payload["sku"],
            "offer_id": payload["offer_id"],
            "provider_identity": payload["provider_identity"],
            "observed_provider_identity": observed_provider_identity,
            "object_generation": payload["object_generation"],
            "graph_id": payload["graph_id"],
            "condition_hash": payload["condition_hash"],
            "content_identity": payload["content_identity"],
            "expected_request_fingerprint": comparison.expected_request_fingerprint,
            "comparison_fingerprint": comparison.comparison_fingerprint,
            "observation_id": preview.observation_id,
            "offer": offer, "inventory_item": inventory,
            "observed_at": observed_at, "operation_id": operation_id,
        }

    def _handle_governed(self, job):
        payload = job.get("payload_json") or {}
        sku = payload.get("sku")
        job_id, lease_token = self._validate_binding(payload, sku, job)
        from tgw.config import sku_json

        path = sku_json(dict(self.config), sku)
        checkpoint = payload.get("observation_checkpoint")
        replaying = checkpoint is not None
        if checkpoint is not None:
            checkpoint = state_machine.checkpoint_running_job(
                job_id, self.owner, lease_token, checkpoint,
            )
            offer = checkpoint.get("offer")
            inventory = checkpoint.get("inventory_item")
            comparison = self._comparison(
                payload, offer, inventory,
                checkpoint.get("observed_provider_identity"),
            )
            expected = self._checkpoint(
                payload, comparison, offer, inventory, checkpoint.get("observed_at"),
                checkpoint.get("observed_provider_identity"),
            )
            if checkpoint != expected:
                raise HardFailure("legacy onboarding checkpoint binding mismatch")
        else:
            read = read_legacy_stage_observation(
                self.config, sku=sku, offer_id=payload["offer_id"],
            )
            if read.outcome != "complete":
                observation = build_provider_observation(
                    provider="ebay", provider_identity=payload["provider_identity"],
                    sku=sku, offer_id=payload["offer_id"],
                    object_generation=payload["object_generation"],
                    graph_id=payload["graph_id"], condition_hash=payload["condition_hash"],
                    content_identity=payload["content_identity"], outcome=read.outcome,
                    evidence={"reason_code": read.reason_code,
                              "http_status": read.http_status},
                    observed_at=datetime.now(UTC).isoformat(),
                )
                observation = record_provider_observation(observation)
                raise TreatmentFailure(
                    "legacy stage provider read did not corroborate staging",
                    self._receipt(payload, sku, read.outcome, read.reason_code,
                                  observation_id=observation.observation_id),
                )
            offer, inventory = read.offer, read.inventory_item
            comparison = self._comparison(
                payload, offer, inventory, read.provider_identity,
            )
            observed_at = datetime.now(UTC).isoformat()
            checkpoint = self._checkpoint(
                payload, comparison, offer, inventory, observed_at,
                read.provider_identity,
            )
            checkpoint = state_machine.checkpoint_running_job(
                job_id, self.owner, lease_token, checkpoint,
            )
            if checkpoint != self._checkpoint(
                payload, comparison, offer, inventory, observed_at,
                read.provider_identity,
            ):
                raise HardFailure("legacy onboarding checkpoint persistence mismatch")
        observation = build_and_record_legacy_stage_observation(
            comparison, config=self.config, sku=sku, offer_id=payload["offer_id"],
            object_generation=payload["object_generation"], graph_id=payload["graph_id"],
            condition_hash=payload["condition_hash"],
            content_identity=payload["content_identity"],
            observed_at=checkpoint["observed_at"],
        )
        if observation.observation_id != checkpoint["observation_id"]:
            raise HardFailure("legacy onboarding observation identity mismatch")
        if comparison.outcome != "corroborated":
            raise TreatmentFailure(
                "legacy stage evidence did not corroborate exact content",
                self._receipt(payload, sku, comparison.outcome,
                              comparison.evidence["reason_code"],
                              observation_id=observation.observation_id),
            )
        mutation = self._apply_marker(
            payload, path, observation.observation_id, checkpoint["operation_id"],
            comparison.comparison_fingerprint,
        )
        if replaying and mutation.status == "REPAIR_REQUIRED":
            from tgw.item_mutation import reconcile_mutation

            mutation = reconcile_mutation(
                item_path=path, journal_root=self._journal_root(),
                operation_id=checkpoint["operation_id"], project=self._project,
            )
        if mutation.status != "COMMITTED":
            outcome = {
                "CONFLICT": "conflict",
                "REPAIR_REQUIRED": "repair_required",
                "FAILED": "failed",
            }.get(mutation.status, "failed")
            raise TreatmentFailure(
                "legacy onboarding local mutation did not commit",
                self._receipt(payload, sku, outcome, f"ITEM_MUTATION_{mutation.status}",
                              observation_id=observation.observation_id,
                              operation_id=mutation.operation_id),
            )
        return self._receipt(
            payload, sku, "satisfied", "LEGACY_STAGE_CORROBORATED",
            observation_id=observation.observation_id,
            operation_id=mutation.operation_id, changed=mutation.changed,
            resulting_generation=mutation.resulting_generation,
        )

    def _journal_root(self):
        data_root = Path(self.config.get("data_root", Path(self.config["itemdata_root"]).parent))
        return Path(self.config.get(
            "item_mutation_journal_root", data_root.parent / "var/item-mutations",
        ))

    def _project(self, _sku, document):
        from tgw.sqlite_catalog import upsert_catalog_row

        result = upsert_catalog_row(dict(self.config), document)
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("SQLite projection did not report success")
        return result

    def _apply_marker(
        self, payload, path, observation_id, operation_id,
        comparison_fingerprint,
    ):
        from tgw.item_mutation import mutate_item

        def mutate(document):
            if document.get("sku") != payload["sku"]:
                raise ValueError("authoritative item SKU mismatch")
            updated = dict(document)
            offer = dict(updated.get("ebay_offer") or {})
            for key in (
                "provider_effect_id", "legacy_stage_observation_id",
                "stage_content_identity",
            ):
                if key in offer and not (
                    isinstance(offer[key], str) and _HEX64.fullmatch(offer[key])
                ):
                    raise ValueError(f"canonical {key} is malformed")
            if offer.get("offer_id") != payload["offer_id"]:
                raise ValueError("canonical offer_id conflicts with corroborated offer")
            if offer.get("provider_effect_id"):
                raise ValueError(
                    "canonical offer already has provider-effect authority"
                )
            for key, value in (
                ("legacy_stage_observation_id", observation_id),
                ("stage_content_identity", payload["content_identity"]),
            ):
                existing = offer.get(key)
                if existing not in (None, "", value):
                    raise ValueError(f"canonical {key} conflicts with corroborated evidence")
                offer[key] = value
            updated["ebay_offer"] = offer
            return updated

        data_root = Path(self.config.get("data_root", Path(self.config["itemdata_root"]).parent))
        return mutate_item(
            item_path=path,
            archive_root=Path(self.config.get("archive_root", data_root / "ItemArchive")),
            journal_root=self._journal_root(), sku=payload["sku"], kind=TREATMENT_ID,
            expected_generation=payload["object_generation"],
            payload={"content_identity": payload["content_identity"],
                     "comparison_fingerprint": comparison_fingerprint,
                     "observation_id": observation_id},
            mutate=mutate, project=self._project, project_noop=True,
            operation_id=operation_id,
        )


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-ebay-onboard-legacy-stage-worker")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--queue", default="ebay_onboard_legacy_stage")
    args = parser.parse_args()
    EbayOnboardLegacyStageWorker(
        args.queue, load_config(Path(args.config)),
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
