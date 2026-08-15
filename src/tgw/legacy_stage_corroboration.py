"""Dormant, read-only corroboration of legacy eBay staged offers."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from urllib.parse import quote

import requests

from tgw.apis.ebay.client import ebay_get
from tgw.provider_observations import (
    ProviderObservation,
    build_provider_observation,
    record_provider_observation,
)


@dataclass(frozen=True)
class LegacyStageComparison:
    sku: str
    offer_id: str
    provider_identity: str
    object_generation: str
    graph_id: str
    condition_hash: str
    content_identity: str
    expected_request_json: str
    expected_request_fingerprint: str
    comparison_fingerprint: str
    outcome: str
    reasons: tuple[str, ...]
    evidence: dict[str, Any]


@dataclass(frozen=True)
class LegacyStageRead:
    outcome: str
    reason_code: str
    offer: dict[str, Any] | None = None
    inventory_item: dict[str, Any] | None = None
    http_status: int | None = None
    provider_identity: str | None = None


class _ShapeError(ValueError):
    def __init__(self, path: str, *, missing: bool = False) -> None:
        super().__init__(path)
        self.path = path
        self.missing = missing


def _comparison_identity(
    *, sku: str, offer_id: str, provider_identity: str,
    object_generation: str, graph_id: str, condition_hash: str,
    content_identity: str, expected_request_fingerprint: str,
    outcome: str, reasons: tuple[str, ...],
) -> str:
    encoded = json.dumps(
        {
            "sku": sku, "offer_id": offer_id,
            "provider_identity": provider_identity,
            "object_generation": object_generation, "graph_id": graph_id,
            "condition_hash": condition_hash, "content_identity": content_identity,
            "expected_request_fingerprint": expected_request_fingerprint,
            "outcome": outcome, "reasons": list(reasons),
        },
        ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _field(value: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in value:
        raise _ShapeError(path, missing=True)
    return value[key]


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _ShapeError(path)
    return value


def _string(value: Any, path: str, *, nonblank: bool = False) -> str:
    if not isinstance(value, str) or (nonblank and not value.strip()):
        raise _ShapeError(path)
    return value


def _quantity(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000:
        raise _ShapeError(path)
    return value


def _positive_number(value: Any, path: str) -> str:
    if isinstance(value, bool):
        raise _ShapeError(path)
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise _ShapeError(path) from None
    if not decimal.is_finite() or decimal <= 0:
        raise _ShapeError(path)
    return format(decimal.normalize(), "f")


def _json(value: Any, path: str) -> Any:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _ShapeError(path)
        return value
    if isinstance(value, list):
        return [_json(child, f"{path}[{index}]") for index, child in enumerate(value)]
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise _ShapeError(path)
        return {key: _json(child, f"{path}.{key}") for key, child in value.items()}
    raise _ShapeError(path)


def _price(value: Any, path: str) -> dict[str, Any]:
    price = _mapping(value, path)
    return {
        "currency": _string(_field(price, "currency", f"{path}.currency"),
                            f"{path}.currency", nonblank=True),
        "value": _positive_number(_field(price, "value", f"{path}.value"),
                                  f"{path}.value"),
    }


def _policies(value: Any) -> dict[str, Any]:
    policies = _mapping(value, "offer.listingPolicies")
    projected = {
        key: _string(
            _field(policies, key, f"offer.listingPolicies.{key}"),
            f"offer.listingPolicies.{key}", nonblank=True,
        )
        for key in ("fulfillmentPolicyId", "paymentPolicyId", "returnPolicyId")
    }
    if "bestOfferTerms" in policies:
        terms = _mapping(policies["bestOfferTerms"], "listingPolicies.bestOfferTerms")
        enabled = _field(terms, "bestOfferEnabled", "bestOfferTerms.bestOfferEnabled")
        if not isinstance(enabled, bool):
            raise _ShapeError("bestOfferTerms.bestOfferEnabled")
        canonical_terms: dict[str, Any] = {"bestOfferEnabled": enabled}
        for key in ("autoAcceptPrice", "autoDeclinePrice"):
            if key in terms:
                canonical_terms[key] = _price(terms[key], f"bestOfferTerms.{key}")
        projected["bestOfferTerms"] = canonical_terms
    return projected


def _ship_to_locations(value: Any) -> dict[str, Any]:
    locations = _mapping(value, "offer.shipToLocations")
    included = _field(locations, "regionIncluded", "shipToLocations.regionIncluded")
    if not isinstance(included, list):
        raise _ShapeError("shipToLocations.regionIncluded")
    return {"regionIncluded": [
        {
            "regionType": _string(
                _field(_mapping(item, "regionIncluded[]"), "regionType",
                       "regionIncluded.regionType"),
                "regionIncluded.regionType", nonblank=True,
            ),
            "regionName": _string(
                _field(_mapping(item, "regionIncluded[]"), "regionName",
                       "regionIncluded.regionName"),
                "regionIncluded.regionName", nonblank=True,
            ),
        }
        for item in included
    ]}


def _offer_projection(value: Any, *, observed: bool) -> dict[str, Any]:
    offer = _mapping(value, "offer")
    projected = {
        key: _string(_field(offer, key, f"offer.{key}"), f"offer.{key}", nonblank=True)
        for key in (
            "sku", "marketplaceId", "format", "categoryId",
            "listingDescription", "merchantLocationKey",
        )
    }
    projected["availableQuantity"] = _quantity(
        _field(offer, "availableQuantity", "offer.availableQuantity"),
        "offer.availableQuantity",
    )
    projected["listingPolicies"] = _policies(
        _field(offer, "listingPolicies", "offer.listingPolicies"),
    )
    projected["shipToLocations"] = _ship_to_locations(
        _field(offer, "shipToLocations", "offer.shipToLocations"),
    )
    summary = _mapping(
        _field(offer, "pricingSummary", "offer.pricingSummary"),
        "offer.pricingSummary",
    )
    pricing = {"price": _price(
        _field(summary, "price", "offer.pricingSummary.price"),
        "offer.pricingSummary.price",
    )}
    if "originalRetailPrice" in summary:
        pricing["originalRetailPrice"] = _price(
            summary["originalRetailPrice"],
            "offer.pricingSummary.originalRetailPrice",
        )
    projected["pricingSummary"] = pricing
    for key in ("secondaryCategoryId", "storeCategoryNames"):
        if key in offer:
            projected[key] = (
                _string(offer[key], f"offer.{key}", nonblank=True)
                if key == "secondaryCategoryId"
                else [_string(item, f"offer.{key}[]", nonblank=True)
                      for item in offer[key]]
                if isinstance(offer[key], list)
                else (_ for _ in ()).throw(_ShapeError(f"offer.{key}"))
            )
    if observed:
        projected["offerId"] = _string(
            _field(offer, "offerId", "offer.offerId"), "offer.offerId",
            nonblank=True,
        )
        projected["status"] = _string(
            _field(offer, "status", "offer.status"), "offer.status",
            nonblank=True,
        )
        if "listing" in offer and offer["listing"] not in (None, {}, ""):
            projected["listing"] = _json(offer["listing"], "offer.listing")
    return projected


def _inventory_projection(value: Any) -> dict[str, Any]:
    inventory = _mapping(value, "inventory")
    product = _mapping(
        _field(inventory, "product", "inventory.product"), "inventory.product",
    )
    aspects = _mapping(
        _field(product, "aspects", "inventory.product.aspects"),
        "inventory.product.aspects",
    )
    product_projection = {
        "title": _string(_field(product, "title", "inventory.product.title"),
                         "inventory.product.title"),
        "description": _string(
            _field(product, "description", "inventory.product.description"),
            "inventory.product.description",
        ),
        "aspects": {
            _string(key, "inventory.product.aspects key", nonblank=True): [
                _string(item, f"inventory.product.aspects.{key}[]")
                for item in value
            ] if isinstance(value, list) else (
                _ for _ in ()
            ).throw(_ShapeError(f"inventory.product.aspects.{key}"))
            for key, value in aspects.items()
        },
        "imageUrls": [
            _string(item, "inventory.product.imageUrls[]", nonblank=True)
            for item in _field(product, "imageUrls", "inventory.product.imageUrls")
        ] if isinstance(_field(product, "imageUrls", "inventory.product.imageUrls"), list)
        else (_ for _ in ()).throw(_ShapeError("inventory.product.imageUrls")),
    }
    if "epid" in product:
        product_projection["epid"] = _string(
            product["epid"], "inventory.product.epid", nonblank=True,
        )
    ship = _mapping(
        _field(_mapping(
            _field(inventory, "availability", "inventory.availability"),
            "inventory.availability",
        ), "shipToLocationAvailability",
               "inventory.availability.shipToLocationAvailability"),
        "inventory.availability.shipToLocationAvailability",
    )
    distributions_value = _field(
        ship, "availabilityDistributions",
        "inventory.availability.shipToLocationAvailability.availabilityDistributions",
    )
    if not isinstance(distributions_value, list):
        raise _ShapeError(
            "inventory.availability.shipToLocationAvailability.availabilityDistributions"
        )
    distributions = []
    for index, raw in enumerate(distributions_value):
        item = _mapping(raw, f"inventory.availability.distributions[{index}]")
        distributions.append({
            "merchantLocationKey": _string(
                _field(item, "merchantLocationKey", "distribution.merchantLocationKey"),
                "distribution.merchantLocationKey", nonblank=True,
            ),
            "quantity": _quantity(
                _field(item, "quantity", "distribution.quantity"),
                "distribution.quantity",
            ),
        })
    projected = {
        "condition": _string(
            _field(inventory, "condition", "inventory.condition"),
            "inventory.condition", nonblank=True,
        ),
        "product": product_projection,
        "availability": {"shipToLocationAvailability": {
            "quantity": _quantity(_field(ship, "quantity", "availability.quantity"),
                                  "availability.quantity"),
            "availabilityDistributions": distributions,
        }},
    }
    if "conditionDescription" in inventory:
        projected["conditionDescription"] = _string(
            inventory["conditionDescription"], "inventory.conditionDescription",
        )
    if "packageWeightAndSize" in inventory:
        package = _mapping(inventory["packageWeightAndSize"],
                           "inventory.packageWeightAndSize")
        weight = _mapping(_field(package, "weight", "package.weight"),
                          "package.weight")
        projected["packageWeightAndSize"] = {"weight": {
            "value": _positive_number(_field(weight, "value", "weight.value"),
                                      "weight.value"),
            "unit": _string(_field(weight, "unit", "weight.unit"), "weight.unit",
                            nonblank=True),
        }}
    return projected


def compare_legacy_stage_observation(
    *, sku: str, offer_id: str, trusted_provider_identity: str,
    object_generation: str, graph_id: str, condition_hash: str,
    content_identity: str,
    observed_provider_identity: str | None, expected_inventory: Mapping[str, Any],
    expected_offer: Mapping[str, Any], observed_inventory: Mapping[str, Any],
    observed_offer: Mapping[str, Any],
) -> LegacyStageComparison:
    """Compare two GET results to the exact canonical stage request bodies."""
    if not all(isinstance(value, str) and value.strip() for value in (
        sku, offer_id, trusted_provider_identity, object_generation, graph_id,
        condition_hash, content_identity,
    )):
        raise ValueError("corroboration identity bindings must be non-empty strings")
    try:
        expected_offer_projection = _offer_projection(expected_offer, observed=False)
        expected_inventory_projection = _inventory_projection(expected_inventory)
    except _ShapeError as exc:
        raise ValueError(f"canonical stage request is invalid at {exc.path}") from exc
    if expected_offer_projection["sku"] != sku:
        raise ValueError("canonical stage request SKU does not match bound sku")
    expected_request_json = json.dumps(
        {"inventory_item": expected_inventory_projection,
         "offer": expected_offer_projection},
        ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    )
    expected_request_fingerprint = hashlib.sha256(
        expected_request_json.encode("utf-8"),
    ).hexdigest()
    try:
        observed_offer_projection = _offer_projection(observed_offer, observed=True)
        observed_inventory_projection = _inventory_projection(observed_inventory)
    except _ShapeError as exc:
        reason = ("MISSING:" if exc.missing else "MALFORMED:") + exc.path
        reasons = (reason,)
        return LegacyStageComparison(
            sku=sku, offer_id=offer_id,
            provider_identity=trusted_provider_identity,
            object_generation=object_generation, graph_id=graph_id,
            condition_hash=condition_hash, content_identity=content_identity,
            expected_request_json=expected_request_json,
            expected_request_fingerprint=expected_request_fingerprint,
            comparison_fingerprint=_comparison_identity(
                sku=sku, offer_id=offer_id,
                provider_identity=trusted_provider_identity,
                object_generation=object_generation, graph_id=graph_id,
                condition_hash=condition_hash, content_identity=content_identity,
                expected_request_fingerprint=expected_request_fingerprint,
                outcome="indeterminate", reasons=reasons,
            ),
            outcome="indeterminate", reasons=reasons,
            evidence={"reason_code": "PROVIDER_RESPONSE_MALFORMED",
                      "reasons": [reason]},
        )
    mismatches = []
    if observed_provider_identity != trusted_provider_identity:
        mismatches.append("provider_identity")
    if observed_offer_projection.pop("offerId") != offer_id:
        mismatches.append("offer.offerId")
    if observed_offer_projection.pop("status") != "UNPUBLISHED":
        mismatches.append("offer.status")
    if "listing" in observed_offer_projection:
        mismatches.append("offer.listing")
        observed_offer_projection.pop("listing")
    if observed_offer_projection != expected_offer_projection:
        mismatches.append("offer.request_projection")
    if observed_inventory_projection != expected_inventory_projection:
        mismatches.append("inventory.request_projection")
    outcome = "contradicted" if mismatches else "corroborated"
    reasons = tuple(f"MISMATCH:{name}" for name in sorted(set(mismatches)))
    return LegacyStageComparison(
        sku=sku, offer_id=offer_id,
        provider_identity=trusted_provider_identity,
        object_generation=object_generation, graph_id=graph_id,
        condition_hash=condition_hash, content_identity=content_identity,
        expected_request_json=expected_request_json,
        expected_request_fingerprint=expected_request_fingerprint,
        comparison_fingerprint=_comparison_identity(
            sku=sku, offer_id=offer_id,
            provider_identity=trusted_provider_identity,
            object_generation=object_generation, graph_id=graph_id,
            condition_hash=condition_hash, content_identity=content_identity,
            expected_request_fingerprint=expected_request_fingerprint,
            outcome=outcome, reasons=reasons,
        ),
        outcome=outcome, reasons=reasons,
        evidence={
            "reason_code": {
                "corroborated": "EXACT_STAGE_MATCH",
                "contradicted": "STAGE_CONTRADICTION",
                "indeterminate": "STAGE_EVIDENCE_INCOMPLETE",
            }[outcome],
            "reasons": list(reasons),
            "observed_offer": dict(observed_offer),
            "observed_inventory_item": dict(observed_inventory),
        },
    )


def build_and_record_legacy_stage_observation(
    comparison: LegacyStageComparison, *, config: Mapping[str, Any], sku: str,
    offer_id: str, object_generation: str, graph_id: str,
    condition_hash: str, content_identity: str, observed_at: str,
    connection: Any | None = None,
) -> ProviderObservation:
    provider_identity = _provider_identity(config)
    supplied = {
        "provider_identity": provider_identity, "sku": sku, "offer_id": offer_id,
        "object_generation": object_generation, "graph_id": graph_id,
        "condition_hash": condition_hash, "content_identity": content_identity,
    }
    if any(getattr(comparison, key) != value for key, value in supplied.items()):
        raise ValueError("comparison lifecycle binding does not match record request")
    try:
        parsed_request = json.loads(comparison.expected_request_json)
        canonical_request = json.dumps(
            parsed_request, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("comparison expected request is not canonical JSON") from exc
    fingerprint = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    if (canonical_request != comparison.expected_request_json
            or fingerprint != comparison.expected_request_fingerprint):
        raise ValueError("comparison expected request fingerprint is invalid")
    comparison_fingerprint = _comparison_identity(
        sku=comparison.sku, offer_id=comparison.offer_id,
        provider_identity=comparison.provider_identity,
        object_generation=comparison.object_generation,
        graph_id=comparison.graph_id, condition_hash=comparison.condition_hash,
        content_identity=comparison.content_identity,
        expected_request_fingerprint=comparison.expected_request_fingerprint,
        outcome=comparison.outcome, reasons=comparison.reasons,
    )
    if comparison_fingerprint != comparison.comparison_fingerprint:
        raise ValueError("comparison lifecycle fingerprint is invalid")
    observation = build_provider_observation(
        provider="ebay", provider_identity=provider_identity, sku=sku,
        offer_id=offer_id, object_generation=object_generation,
        graph_id=graph_id, condition_hash=condition_hash,
        content_identity=content_identity, outcome=comparison.outcome,
        evidence={
            **comparison.evidence,
            "expected_request_fingerprint": comparison.expected_request_fingerprint,
        },
        observed_at=observed_at,
    )
    return record_provider_observation(observation, connection=connection)


def read_legacy_stage_observation(
    config: Mapping[str, Any], *, sku: str, offer_id: str,
) -> LegacyStageRead:
    """Perform exactly two read-only GETs; classify failures without mutation."""
    if not isinstance(sku, str) or not sku.strip():
        raise ValueError("sku must be a non-empty string")
    if not isinstance(offer_id, str) or not offer_id.strip():
        raise ValueError("offer_id must be a non-empty string")
    try:
        provider_identity = _provider_identity(config)
    except ValueError:
        return LegacyStageRead("indeterminate", "PROVIDER_IDENTITY_UNCONFIGURED")
    quoted_offer = quote(offer_id, safe="")
    quoted_sku = quote(sku, safe="")
    try:
        offer = ebay_get(dict(config), f"/sell/inventory/v1/offer/{quoted_offer}")
        inventory = ebay_get(
            dict(config), f"/sell/inventory/v1/inventory_item/{quoted_sku}",
        )
    except (requests.ConnectionError, requests.Timeout):
        return LegacyStageRead("indeterminate", "PROVIDER_TRANSPORT_ERROR")
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status == 404:
            return LegacyStageRead(
                "contradicted", "PROVIDER_STAGE_NOT_FOUND", http_status=status,
            )
        if status in {401, 403}:
            return LegacyStageRead(
                "indeterminate", "PROVIDER_AUTHORIZATION_FAILED", http_status=status,
            )
        return LegacyStageRead(
            "indeterminate", "PROVIDER_READ_ERROR", http_status=status,
        )
    if not isinstance(offer, dict) or not isinstance(inventory, dict):
        return LegacyStageRead("indeterminate", "PROVIDER_RESPONSE_MALFORMED")
    return LegacyStageRead(
        "complete", "PROVIDER_READ_COMPLETE", offer=dict(offer),
        inventory_item=dict(inventory), provider_identity=provider_identity,
    )


def _provider_identity(config: Mapping[str, Any]) -> str:
    migration = config.get("workflow_migration")
    if migration is None and isinstance(config.get("raw"), Mapping):
        migration = config["raw"].get("workflow_migration")
    value = migration.get("ebay_provider_identity") if isinstance(
        migration, Mapping,
    ) else None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("trusted eBay provider identity is not configured")
    return value
