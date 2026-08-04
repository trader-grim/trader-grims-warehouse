"""Tests for tgw.ebay.sync.extract_ebay_error_field() — PP-CONDITION-ENUM-001 /
todo #1562.

Live incident: tgw202605051124483 dead-lettered at ebay_stage with only eBay's
generic wrapper text surfaced ("The request has errors. For help, see the
documentation for this API."); the real reason
("Could not serialize field [condition]") was sitting unused in
errors[0].parameters[0]. This exercises the extraction against that exact
body shape plus a couple of adjacent/edge shapes, all offline.
"""

from __future__ import annotations

import json

from tgw.ebay.sync import extract_ebay_error_field

# Verbatim (captured live) raw eBay error body from tgw202605051124483's
# pipeline_error.raw, 2026-07-19.
_LIVE_INCIDENT_BODY = (
    '{"errors":[{"errorId":2004,"domain":"ACCESS","category":"REQUEST",'
    '"message":"Invalid request","longMessage":"The request has errors. '
    'For help, see the documentation for this API.",'
    '"parameters":[{"name":"reason","value":"Could not serialize field [condition]"}]}]}'
)


def test_extracts_and_maps_field_from_live_incident_body():
    # eBay names its own field "condition"; mapped to our draft_listing key.
    assert extract_ebay_error_field(_LIVE_INCIDENT_BODY) == "condition_enum"


def test_structured_fieldname_parameter_used_directly():
    body = json.dumps({
        "errors": [{
            "message": "Invalid value",
            "parameters": [{"name": "fieldName", "value": "someOtherField"}],
        }]
    })
    assert extract_ebay_error_field(body) == "someOtherField"


def test_bracket_fallback_against_longmessage_when_no_parameters():
    body = json.dumps({
        "errors": [{
            "longMessage": "Could not serialize field [price]",
        }]
    })
    assert extract_ebay_error_field(body) == "price"


def test_no_field_reference_returns_none_not_a_guess():
    body = json.dumps({"errors": [{"message": "Something went wrong"}]})
    assert extract_ebay_error_field(body) is None


def test_malformed_body_returns_none_never_raises():
    assert extract_ebay_error_field("not json at all") is None
    assert extract_ebay_error_field("") is None


def test_no_errors_key_returns_none():
    assert extract_ebay_error_field(json.dumps({})) is None
