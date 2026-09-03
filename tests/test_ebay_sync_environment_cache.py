from __future__ import annotations

import tgw.ebay.sync as sync


def test_account_caches_are_partitioned_by_ebay_environment(monkeypatch):
    sync._policies_cache.clear()
    sync._location_cache.clear()
    sync._store_categories_cache.clear()
    calls: list[tuple[str, str]] = []

    def fake_get(cfg, path, params=None):
        environment = cfg.get("ebay_environment", "production")
        calls.append((environment, path))
        if "fulfillment_policy" in path:
            return {
                "fulfillmentPolicies": [
                    {"fulfillmentPolicyId": f"fulfillment-{environment}"}
                ]
            }
        if "payment_policy" in path:
            return {
                "paymentPolicies": [{"paymentPolicyId": f"payment-{environment}"}]
            }
        if "return_policy" in path:
            return {
                "returnPolicies": [{"returnPolicyId": f"return-{environment}"}]
            }
        if path == "/sell/inventory/v1/location":
            return {
                "locations": [{
                    "merchantLocationKey": f"location-{environment}",
                    "merchantLocationStatus": "ENABLED",
                }]
            }
        raise AssertionError(path)

    monkeypatch.setattr(sync, "ebay_get", fake_get)

    import tgw.apis.ebay.trading as trading

    monkeypatch.setattr(
        trading,
        "get_store_categories",
        lambda cfg: [{
            "id": cfg.get("ebay_environment", "production"),
            "name": cfg.get("ebay_environment", "production"),
        }],
    )

    production = {"ebay_environment": "production"}
    sandbox = {"ebay_environment": "sandbox"}

    assert sync._get_policies(production)["fulfillmentPolicyId"] == (
        "fulfillment-production"
    )
    assert sync._get_policies(sandbox)["fulfillmentPolicyId"] == (
        "fulfillment-sandbox"
    )
    assert sync._get_merchant_location(production) == "location-production"
    assert sync._get_merchant_location(sandbox) == "location-sandbox"
    assert sync._get_store_categories_cached(production)[0]["id"] == "production"
    assert sync._get_store_categories_cached(sandbox)[0]["id"] == "sandbox"

    first_call_count = len(calls)
    assert sync._get_policies(production)["fulfillmentPolicyId"] == (
        "fulfillment-production"
    )
    assert sync._get_merchant_location(production) == "location-production"
    assert sync._get_store_categories_cached(production)[0]["id"] == "production"
    assert len(calls) == first_call_count
