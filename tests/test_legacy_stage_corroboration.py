import copy
import inspect
from dataclasses import replace
from unittest.mock import patch

import pytest
import requests

from tgw.legacy_stage_corroboration import (
    build_and_record_legacy_stage_observation,
    compare_legacy_stage_observation,
    read_legacy_stage_observation,
)


def _bodies():
    inventory = {
        "condition": "USED_EXCELLENT",
        "product": {
            "title": "Exact title", "description": "Exact description",
            "aspects": {"Brand": ["Example"], "Type": ["Part"]},
            "imageUrls": ["https://img/1", "https://img/2"],
        },
        "availability": {"shipToLocationAvailability": {
            "quantity": 2,
            "availabilityDistributions": [
                {"merchantLocationKey": "warehouse-1", "quantity": 2},
            ],
        }},
    }
    offer = {
        "sku": "SKU-1", "offerId": "OFF-1", "status": "UNPUBLISHED",
        "marketplaceId": "EBAY_US", "format": "FIXED_PRICE",
        "merchantLocationKey": "warehouse-1", "availableQuantity": 2,
        "categoryId": "123", "listingDescription": "Full description",
        "listingPolicies": {
            "fulfillmentPolicyId": "fulfill-1", "paymentPolicyId": "pay-1",
            "returnPolicyId": "return-1",
        },
        "pricingSummary": {"price": {"currency": "USD", "value": "10.00"}},
        "shipToLocations": {
            "regionIncluded": [{"regionType": "COUNTRY", "regionName": "US"}],
        },
    }
    return inventory, offer


def _compare(**changes):
    inventory, offer = _bodies()
    values = {
        "sku": "SKU-1", "offer_id": "OFF-1",
        "trusted_provider_identity": "ebay:account-1",
        "observed_provider_identity": "ebay:account-1",
        "object_generation": "generation-1", "graph_id": "graph-1",
        "condition_hash": "condition-1", "content_identity": "content-1",
        "expected_inventory": inventory, "expected_offer": offer,
        "observed_inventory": copy.deepcopy(inventory),
        "observed_offer": copy.deepcopy(offer),
    }
    values.update(changes)
    return compare_legacy_stage_observation(**values)


def _set_nested(value, path, replacement):
    updated = copy.deepcopy(value)
    current = updated
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = replacement
    return updated


def test_exact_stage_request_and_observation_corroborate():
    result = _compare()
    assert result.outcome == "corroborated"
    assert result.reasons == ()
    assert result.evidence["reason_code"] == "EXACT_STAGE_MATCH"


@pytest.mark.parametrize(
    "field,path,replacement",
    [
        ("observed_offer", ("sku",), "OTHER"),
        ("observed_offer", ("offerId",), "OTHER"),
        ("observed_offer", ("marketplaceId",), "EBAY_MOTORS"),
        ("observed_offer", ("status",), "PUBLISHED"),
        ("observed_offer", ("availableQuantity",), 3),
        ("observed_offer", ("categoryId",), "999"),
        ("observed_offer", ("listingDescription",), "changed"),
        ("observed_offer", ("listingPolicies", "fulfillmentPolicyId"), "other"),
        ("observed_offer", ("pricingSummary", "price", "value"), "11.00"),
        ("observed_offer", ("pricingSummary", "price", "currency"), "CAD"),
        ("observed_inventory", ("condition",), "NEW"),
        ("observed_inventory", ("product", "title"), "changed"),
        ("observed_inventory", ("product", "description"), "changed"),
        ("observed_inventory", ("product", "aspects"), {"Brand": ["Other"]}),
        ("observed_inventory", ("product", "imageUrls"), ["https://img/2"]),
        ("observed_inventory", (
            "availability", "shipToLocationAvailability", "quantity",
        ), 3),
        ("observed_inventory", (
            "availability", "shipToLocationAvailability",
            "availabilityDistributions",
        ), [{"merchantLocationKey": "other", "quantity": 2}]),
    ],
)
def test_each_provider_field_mismatch_is_contradicted(field, path, replacement):
    inventory, offer = _bodies()
    source = inventory if field == "observed_inventory" else offer
    result = _compare(**{field: _set_nested(source, path, replacement)})
    assert result.outcome == "contradicted"
    assert any(reason.startswith("MISMATCH:") for reason in result.reasons)


def test_provider_identity_and_listing_contradictions_fail_exact_match():
    assert _compare(observed_provider_identity="ebay:other").outcome == "contradicted"
    _, offer = _bodies()
    offer["listing"] = {"listingId": "LIVE-1"}
    result = _compare(observed_offer=offer)
    assert result.outcome == "contradicted"
    assert "MISMATCH:offer.listing" in result.reasons


def test_equivalent_money_format_is_normalized():
    _, offer = _bodies()
    offer["pricingSummary"]["price"]["value"] = 10
    assert _compare(observed_offer=offer).outcome == "corroborated"


@pytest.mark.parametrize(
    "surface,path,value",
    [
        ("offer", ("secondaryCategoryId",), "456"),
        ("offer", ("storeCategoryNames",), ["Parts", "Collectibles"]),
        ("offer", ("pricingSummary", "originalRetailPrice"),
         {"currency": "USD", "value": "20.00"}),
        ("inventory", ("conditionDescription",), "Minor wear"),
        ("inventory", ("packageWeightAndSize",),
         {"weight": {"value": 8, "unit": "OUNCE"}}),
        ("inventory", ("product", "epid"), "EPID-1"),
    ],
)
def test_every_optional_request_field_preserves_presence_and_value(
    surface, path, value,
):
    inventory, offer = _bodies()
    target = inventory if surface == "inventory" else offer
    expected = _set_nested(target, path, value)
    exact_kwargs = {
        f"expected_{surface}": expected,
        f"observed_{surface}": copy.deepcopy(expected),
    }
    assert _compare(**exact_kwargs).outcome == "corroborated"
    absent_kwargs = dict(exact_kwargs)
    absent_kwargs[f"observed_{surface}"] = copy.deepcopy(target)
    assert _compare(**absent_kwargs).outcome == "contradicted"


def test_provider_owned_output_fields_are_explicitly_ignored():
    inventory, offer = _bodies()
    offer.update({"createdDate": "provider-time", "listingDuration": "GTC"})
    inventory["locale"] = "en-US"
    assert _compare(
        observed_offer=offer, observed_inventory=inventory,
    ).outcome == "corroborated"


def test_mapping_key_order_is_irrelevant_but_image_order_is_material():
    inventory, offer = _bodies()
    inventory["product"]["aspects"] = {
        "Type": ["Part"], "Brand": ["Example"],
    }
    assert _compare(observed_inventory=inventory).outcome == "corroborated"
    inventory["product"]["imageUrls"].reverse()
    assert _compare(observed_inventory=inventory).outcome == "contradicted"


def test_best_offer_money_is_canonicalized_and_shape_checked():
    _, offer = _bodies()
    terms = {
        "bestOfferEnabled": True,
        "autoAcceptPrice": {"currency": "USD", "value": "8.00"},
    }
    expected = _set_nested(offer, ("listingPolicies", "bestOfferTerms"), terms)
    observed = copy.deepcopy(expected)
    observed["listingPolicies"]["bestOfferTerms"]["autoAcceptPrice"]["value"] = 8
    assert _compare(expected_offer=expected, observed_offer=observed).outcome == (
        "corroborated"
    )
    observed["listingPolicies"]["bestOfferTerms"]["bestOfferEnabled"] = 1
    assert _compare(expected_offer=expected, observed_offer=observed).outcome == (
        "indeterminate"
    )


@pytest.mark.parametrize("value", [True, 1.5, -1, 1_000_001])
def test_quantity_rejects_bool_noninteger_negative_or_unbounded(value):
    _, offer = _bodies()
    offer["availableQuantity"] = value
    assert _compare(observed_offer=offer).outcome == "indeterminate"


@pytest.mark.parametrize("value", [True, 0, -1, float("nan"), float("inf")])
def test_money_rejects_bool_nonpositive_or_nonfinite(value):
    _, offer = _bodies()
    offer["pricingSummary"]["price"]["value"] = value
    assert _compare(observed_offer=offer).outcome == "indeterminate"


def test_missing_or_malformed_provider_evidence_is_indeterminate():
    _, offer = _bodies()
    del offer["listingPolicies"]
    missing = _compare(observed_offer=offer)
    assert missing.outcome == "indeterminate"
    assert "MISSING:offer.listingPolicies" in missing.reasons
    malformed = _compare(observed_inventory="not-an-object")
    assert malformed.outcome == "indeterminate"
    assert malformed.evidence["reason_code"] == "PROVIDER_RESPONSE_MALFORMED"


def test_comparison_builds_and_records_exact_provider_observation():
    comparison = _compare()
    recorded = []
    with patch(
        "tgw.legacy_stage_corroboration.record_provider_observation",
        side_effect=lambda observation, **kwargs: recorded.append(
            (observation, kwargs),
        ) or observation,
    ):
        observation = build_and_record_legacy_stage_observation(
            comparison, config={"workflow_migration": {
                "ebay_provider_identity": "ebay:account-1",
            }}, sku="SKU-1",
            offer_id="OFF-1", object_generation="generation-1",
            graph_id="graph-1", condition_hash="condition-1",
            content_identity="content-1",
            observed_at="2026-08-10T12:00:00Z", connection="connection",
        )
    assert observation.outcome == "corroborated"
    assert observation.evidence == {
        **comparison.evidence,
        "expected_request_fingerprint": comparison.expected_request_fingerprint,
    }
    assert recorded[0][1] == {"connection": "connection"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("sku", "OTHER"), ("offer_id", "OTHER"),
        ("object_generation", "generation-2"), ("graph_id", "graph-2"),
        ("condition_hash", "condition-2"),
        ("content_identity", "content-2"),
    ],
)
def test_comparison_for_one_binding_cannot_record_another(field, value):
    comparison = _compare()
    arguments = {
        "config": {"workflow_migration": {
            "ebay_provider_identity": "ebay:account-1",
        }},
        "sku": "SKU-1", "offer_id": "OFF-1",
        "object_generation": "generation-1", "graph_id": "graph-1",
        "condition_hash": "condition-1", "content_identity": "content-1",
        "observed_at": "2026-08-10T12:00:00Z", "connection": "connection",
    }
    arguments[field] = value
    with pytest.raises(ValueError, match="lifecycle binding"):
        build_and_record_legacy_stage_observation(comparison, **arguments)


def test_comparison_cannot_record_under_another_configured_provider_identity():
    comparison = _compare()
    with pytest.raises(ValueError, match="lifecycle binding"):
        build_and_record_legacy_stage_observation(
            comparison,
            config={"workflow_migration": {
                "ebay_provider_identity": "ebay:other-account",
            }},
            sku="SKU-1", offer_id="OFF-1", object_generation="generation-1",
            graph_id="graph-1", condition_hash="condition-1",
            content_identity="content-1", observed_at="2026-08-10T12:00:00Z",
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_request_fingerprint", "0" * 64),
        ("expected_request_json", '{"forged":true}'),
    ],
)
def test_forged_comparison_request_identity_is_rejected(field, value):
    comparison = replace(_compare(), **{field: value})
    with pytest.raises(ValueError, match="fingerprint"):
        build_and_record_legacy_stage_observation(
            comparison,
            config={"workflow_migration": {
                "ebay_provider_identity": "ebay:account-1",
            }},
            sku="SKU-1", offer_id="OFF-1", object_generation="generation-1",
            graph_id="graph-1", condition_hash="condition-1",
            content_identity="content-1", observed_at="2026-08-10T12:00:00Z",
        )


def test_forged_frozen_comparison_lifecycle_binding_is_rejected():
    comparison = replace(_compare(), sku="OTHER")
    with pytest.raises(ValueError, match="lifecycle fingerprint"):
        build_and_record_legacy_stage_observation(
            comparison,
            config={"workflow_migration": {
                "ebay_provider_identity": "ebay:account-1",
            }},
            sku="OTHER", offer_id="OFF-1", object_generation="generation-1",
            graph_id="graph-1", condition_hash="condition-1",
            content_identity="content-1", observed_at="2026-08-10T12:00:00Z",
        )


def test_expected_offer_sku_must_match_bound_sku():
    _, offer = _bodies()
    offer["sku"] = "OTHER"
    with pytest.raises(ValueError, match="SKU"):
        _compare(expected_offer=offer)


@pytest.mark.parametrize("identity", [None, ""])
def test_absent_observed_provider_identity_cannot_corroborate(identity):
    result = _compare(observed_provider_identity=identity)
    assert result.outcome == "contradicted"
    assert "MISMATCH:provider_identity" in result.reasons


def _http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"HTTP {status}", response=response)


def test_read_helper_performs_only_exact_offer_and_inventory_gets():
    inventory, offer = _bodies()
    with patch(
        "tgw.legacy_stage_corroboration.ebay_get",
        side_effect=[offer, inventory],
    ) as get:
        result = read_legacy_stage_observation(
            {"workflow_migration": {"ebay_provider_identity": "ebay:account-1"}},
            sku="SKU-1", offer_id="OFF-1",
        )
    assert result.outcome == "complete"
    assert result.provider_identity == "ebay:account-1"
    assert [call.args[1] for call in get.call_args_list] == [
        "/sell/inventory/v1/offer/OFF-1",
        "/sell/inventory/v1/inventory_item/SKU-1",
    ]


@pytest.mark.parametrize(
    "error,outcome,reason",
    [
        (_http_error(404), "contradicted", "PROVIDER_STAGE_NOT_FOUND"),
        (_http_error(401), "indeterminate", "PROVIDER_AUTHORIZATION_FAILED"),
        (_http_error(403), "indeterminate", "PROVIDER_AUTHORIZATION_FAILED"),
        (_http_error(500), "indeterminate", "PROVIDER_READ_ERROR"),
        (requests.Timeout("timeout"), "indeterminate", "PROVIDER_TRANSPORT_ERROR"),
        (requests.ConnectionError("offline"), "indeterminate", "PROVIDER_TRANSPORT_ERROR"),
    ],
)
def test_read_helper_classifies_http_auth_and_transport(error, outcome, reason):
    with patch("tgw.legacy_stage_corroboration.ebay_get", side_effect=error):
        result = read_legacy_stage_observation(
            {"workflow_migration": {"ebay_provider_identity": "ebay:account-1"}},
            sku="SKU-1", offer_id="OFF-1",
        )
    assert (result.outcome, result.reason_code) == (outcome, reason)


@pytest.mark.parametrize("responses", [(None, {}), ({}, []), ("bad", {})])
def test_read_helper_classifies_malformed_success(responses):
    with patch(
        "tgw.legacy_stage_corroboration.ebay_get", side_effect=responses,
    ):
        result = read_legacy_stage_observation(
            {"workflow_migration": {"ebay_provider_identity": "ebay:account-1"}},
            sku="SKU-1", offer_id="OFF-1",
        )
    assert result.outcome == "indeterminate"
    assert result.reason_code == "PROVIDER_RESPONSE_MALFORMED"


def test_read_helper_source_has_no_provider_write_or_canonical_mutation_calls():
    source = inspect.getsource(read_legacy_stage_observation)
    for forbidden in (
        "ebay_post", "ebay_put", "ebay_delete", "fence_", "patch_item",
        "provider_effect", "enqueue_job",
    ):
        assert forbidden not in source


def test_read_helper_quotes_untrusted_path_segments_with_no_safe_characters():
    inventory, offer = _bodies()
    with patch(
        "tgw.legacy_stage_corroboration.ebay_get",
        side_effect=[offer, inventory],
    ) as get:
        read_legacy_stage_observation(
            {"workflow_migration": {"ebay_provider_identity": "ebay:account-1"}},
            sku="SKU /?#1", offer_id="OFF/../?#1",
        )
    assert get.call_args_list[0].args[1].endswith("OFF%2F..%2F%3F%231")
    assert get.call_args_list[1].args[1].endswith("SKU%20%2F%3F%231")


@pytest.mark.parametrize("field,value", [("sku", ""), ("offer_id", " ")])
def test_read_helper_rejects_blank_path_identity_before_get(field, value):
    values = {"sku": "SKU-1", "offer_id": "OFF-1", field: value}
    with patch("tgw.legacy_stage_corroboration.ebay_get") as get, \
         pytest.raises(ValueError):
        read_legacy_stage_observation(
            {"workflow_migration": {"ebay_provider_identity": "ebay:account-1"}},
            **values,
        )
    get.assert_not_called()


def test_read_helper_requires_config_derived_provider_identity_before_get():
    with patch("tgw.legacy_stage_corroboration.ebay_get") as get:
        result = read_legacy_stage_observation(
            {}, sku="SKU-1", offer_id="OFF-1",
        )
    assert result.outcome == "indeterminate"
    assert result.reason_code == "PROVIDER_IDENTITY_UNCONFIGURED"
    get.assert_not_called()
