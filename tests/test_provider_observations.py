from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tgw.provider_observations import (
    LEGACY_STAGE_RECEIPT_SCHEMA,
    ProviderObservationConflict,
    build_provider_observation,
    record_provider_observation,
    resolve_legacy_stage_corroboration,
)


def _observation(**changes):
    values = {
        "provider": "ebay",
        "provider_identity": "ebay:account-1",
        "sku": "SKU-1",
        "offer_id": "OFF-1",
        "object_generation": "generation-1",
        "graph_id": "graph-1",
        "condition_hash": "condition-1",
        "content_identity": "content-1",
        "outcome": "corroborated",
        "evidence": {"offer_status": "UNPUBLISHED", "quantity": 1},
        "observed_at": "2026-08-10T12:00:00Z",
    }
    values.update(changes)
    return build_provider_observation(**values)


def _row(observation):
    row = asdict(observation)
    row["evidence_json"] = row.pop("evidence")
    return row


def _connection(row):
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor
    return connection, cursor


def test_observation_identity_is_deterministic_and_exactly_bound():
    first = _observation()
    assert first.observation_id == _observation().observation_id
    assert first.observation_id != _observation(offer_id="OFF-2").observation_id
    assert first.observation_id != _observation(outcome="contradicted").observation_id
    assert first.observation_id != _observation(
        evidence={"offer_status": "UNPUBLISHED", "quantity": True},
    ).observation_id


@pytest.mark.parametrize(
    "field",
    ["sku", "offer_id", "provider_identity", "object_generation", "graph_id",
     "condition_hash", "content_identity", "observed_at"],
)
def test_required_bindings_are_nonempty(field):
    with pytest.raises(ValueError, match=field):
        _observation(**{field: ""})


def test_typed_legacy_stage_receipt_requires_corroborated_outcome():
    outcome = "corroborated"
    observation = _observation(outcome=outcome)
    receipt = resolve_legacy_stage_corroboration(
        observation, sku="SKU-1", offer_id="OFF-1", provider="ebay",
        provider_identity="ebay:account-1", object_generation="generation-1",
        graph_id="graph-1", condition_hash="condition-1",
        content_identity="content-1",
    )
    assert receipt.receipt_schema_id == LEGACY_STAGE_RECEIPT_SCHEMA
    assert receipt.observation_id == observation.observation_id
    assert receipt.outcome == outcome
    assert receipt.evidence == observation.evidence
    assert "authority" not in asdict(receipt)
    assert "provider_effect_id" not in asdict(receipt)


@pytest.mark.parametrize("outcome", ["contradicted", "indeterminate"])
def test_authoritative_resolver_rejects_non_corroborated_outcome(outcome):
    observation = _observation(outcome=outcome)
    with pytest.raises(ProviderObservationConflict, match="not corroborated"):
        resolve_legacy_stage_corroboration(
            observation, sku="SKU-1", offer_id="OFF-1", provider="ebay",
            provider_identity="ebay:account-1", object_generation="generation-1",
            graph_id="graph-1", condition_hash="condition-1",
            content_identity="content-1",
        )


@pytest.mark.parametrize("value", ["not-a-time", "2026-08-10T12:00:00"])
def test_observed_at_rejects_malformed_or_naive_time(value):
    with pytest.raises(ValueError, match="observed_at"):
        _observation(observed_at=value)


def test_observed_at_normalizes_equivalent_offsets_before_identity():
    utc = _observation(observed_at="2026-08-10T12:00:00Z")
    offset = _observation(observed_at="2026-08-10T05:00:00-07:00")
    assert utc.observed_at == "2026-08-10T12:00:00.000000Z"
    assert offset == utc


def test_authoritative_resolver_requires_ebay_provider():
    observation = _observation(provider="other")
    with pytest.raises(ProviderObservationConflict, match="must be ebay"):
        resolve_legacy_stage_corroboration(
            observation, sku="SKU-1", offer_id="OFF-1", provider="other",
            provider_identity="ebay:account-1", object_generation="generation-1",
            graph_id="graph-1", condition_hash="condition-1",
            content_identity="content-1",
        )


@pytest.mark.parametrize(
    "field,value",
    [("sku", "OTHER"), ("offer_id", "OTHER"),
     ("provider_identity", "ebay:other"),
     ("object_generation", "newer"), ("graph_id", "other-graph"),
     ("condition_hash", "other-condition"),
     ("content_identity", "other-content")],
)
def test_resolver_rejects_any_binding_mismatch(field, value):
    observation = _observation()
    expected = {
        "sku": "SKU-1", "offer_id": "OFF-1", "provider": "ebay",
        "provider_identity": "ebay:account-1",
        "object_generation": "generation-1", "graph_id": "graph-1",
        "condition_hash": "condition-1", "content_identity": "content-1",
    }
    expected[field] = value
    with pytest.raises(ProviderObservationConflict, match="binding mismatch"):
        resolve_legacy_stage_corroboration(observation, **expected)


def test_resolver_rejects_forged_observation_identity():
    with pytest.raises(ProviderObservationConflict, match="identity mismatch"):
        resolve_legacy_stage_corroboration(
            replace(_observation(), observation_id="0" * 64),
            sku="SKU-1", offer_id="OFF-1", provider="ebay",
            provider_identity="ebay:account-1",
            object_generation="generation-1", graph_id="graph-1",
            condition_hash="condition-1", content_identity="content-1",
        )


def test_repository_insert_and_exact_replay_are_idempotent():
    observation = _observation()
    connection, cursor = _connection(_row(observation))
    assert record_provider_observation(
        observation, connection=connection,
    ) == observation
    assert cursor.execute.call_count == 2
    assert "ON CONFLICT (observation_id) DO NOTHING" in (
        cursor.execute.call_args_list[0].args[0]
    )
    assert cursor.execute.call_args_list[1].args[1] == (
        observation.observation_id,
    )


def test_repository_detects_durable_same_id_mismatch():
    observation = _observation()
    durable = _row(observation)
    durable["offer_id"] = "FORGED"
    connection, _ = _connection(durable)
    with pytest.raises(ProviderObservationConflict, match="durable"):
        record_provider_observation(observation, connection=connection)


def test_repository_rejects_caller_forged_identity_before_database():
    observation = replace(_observation(), observation_id="f" * 64)
    connection = MagicMock()
    with pytest.raises(ProviderObservationConflict, match="identity"):
        record_provider_observation(observation, connection=connection)
    connection.cursor.assert_not_called()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "bad"}, object()])
def test_evidence_must_be_exact_json_native(value):
    with pytest.raises((TypeError, ValueError)):
        _observation(evidence={"value": value})


def _table_contract(sql: str) -> str:
    start = sql.index("provider_observations (")
    end = sql.index(");", start)
    return " ".join(
        sql[start:end].replace("public.", "").replace("IF NOT EXISTS ", "").lower().split()
    )


def test_source_and_live_schema_provider_observation_contracts_match():
    root = Path(__file__).parents[1]
    source = (root / "src/tgw/queue/schema.sql").read_text()
    live = (root / "src/tgw/queue/live_schema.sql").read_text()
    source_contract = _table_contract(source)
    live_contract = _table_contract(live)
    assert source_contract == live_contract
    for column in (
        "observation_id", "schema_id", "observation_type", "provider",
        "provider_identity", "sku", "offer_id", "object_generation", "graph_id",
        "condition_hash", "content_identity", "outcome", "evidence_json",
        "observed_at", "created_at",
    ):
        assert column in source_contract
        assert column in live_contract
    assert "provider-observation/v1" in source_contract
    assert "provider-observation/v1" in live_contract
    assert "corroborated" in source_contract and "corroborated" in live_contract
