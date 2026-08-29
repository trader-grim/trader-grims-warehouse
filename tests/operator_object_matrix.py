"""Generate the cross-client operator-object parity fixture from server code."""

from __future__ import annotations

import json
from pathlib import Path

from tgw.item_mutation import item_generation
from tgw.operator_objects import build_item_operator_object


def _item(state: str) -> dict:
    item = {
        "sku": f"sku-{state}",
        "title": f"{state.replace('_', ' ').title()} item",
        "draft_listing": {
            "category_id": "123",
            "condition_enum": "USED_GOOD",
            "condition_label": "Used - Good",
            "item_specifics": {"Brand": "TGW"},
        },
    }
    if state in {"staged", "published"}:
        item["ebay_offer"] = {
            "offer_id": f"offer-{state}",
            "status": "PUBLISHED" if state == "published" else "UNPUBLISHED",
        }
    if state == "published":
        item["ebay_listing"] = {"listing_id": "listing-published", "status": "Active"}
    if state == "held":
        item["draft_listing"] = {"category_id": "99", "item_specifics": {}}
    return item


def _workflow(state: str, item: dict) -> dict:
    enabled = {
        "ready": {"save-draft", "list-item"},
        "staged": {"save-draft", "list-item", "update-item"},
        "published": {"save-draft", "update-item"},
        "held": {"save-draft"},
        "in_progress": set(),
        "reconciliation_required": {"save-draft"},
    }[state]
    reason = {
        "published": "The provider already reports this item as published.",
        "held": "The evaluator exposes no legal listing action.",
        "in_progress": "The authoritative workflow is active.",
        "reconciliation_required": "provider evidence is stale",
    }.get(state)
    return {
        "entity_id": f"sku-{state}",
        "object_generation": item_generation(item),
        "graph_id": f"graph-{state}",
        "fingerprints": [],
        "attempts": [],
        "active_attempts": ["job-active"] if state == "in_progress" else [],
        "reconciliation_gates": ["provider evidence is stale"] if state == "reconciliation_required" else [],
        "ownership_conflicts": [],
        "operator_gates": [],
        "legal_actions": [] if state in {"published", "held", "in_progress", "reconciliation_required"} else [
            {
                "treatment_id": "ebay-publish",
                "treatment_version": "1",
                "effect_class": "external",
                "action": "held_external_contract",
                "reasons": [],
            },
        ],
        "operator_projection": {
            "state": state,
            "reasons": [reason] if state == "reconciliation_required" else [],
            "commands": {
                command_id: {
                    "enabled": command_id in enabled,
                    "reason": None if command_id in enabled else reason,
                }
                for command_id in ("save-draft", "list-item", "update-item")
            },
        },
    }


def generated_matrix() -> list[dict]:
    context = {
        "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}],
        "aspects": [{"name": "Brand", "required": True, "allowed_values": []}],
    }
    expectations = {
        "ready": ["save-inventory", "save-listing-draft", "list-item"],
        "staged": ["save-inventory", "save-listing-draft", "list-item", "update-item"],
        "published": ["save-inventory", "save-listing-draft", "update-item"],
        "held": ["save-inventory", "save-listing-draft"],
        "in_progress": [],
        "reconciliation_required": ["save-inventory", "save-listing-draft"],
    }
    rows = []
    for requested_state, enabled in expectations.items():
        item = _item(requested_state)
        published = build_item_operator_object(
            item=item,
            workflow_card=_workflow(requested_state, item),
            category_context=context,
        )
        assert published["workflow"]["state"] == requested_state
        rows.append(
            {
                "object": published,
                "expected": {
                    "state": requested_state,
                    "reasons": published["workflow"]["reasons"],
                    "enabled_commands": enabled,
                    "authority_scopes": {
                        command["id"]: command["authority_scope"]
                        for command in published["commands"]
                    },
                    "field_schema_keys": sorted(published["field_schema"]),
                },
            }
        )
    return rows


def encoded_matrix() -> str:
    return json.dumps(generated_matrix(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    (Path(__file__).parent / "fixtures/operator_object_state_matrix.json").write_text(
        encoded_matrix(), encoding="utf-8",
    )
