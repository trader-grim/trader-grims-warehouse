from datetime import UTC, datetime, timedelta

from tgw.workflow.operator_authority import (
    OperatorAuthority,
    get_authority,
    issue_authority,
    supersede_authority,
    validate_authority,
    validate_provider_effect_authority,
)


class Cursor:
    def __init__(self, row=None, rowcount=1):
        self.row, self.rowcount, self.calls = row, rowcount, []
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def execute(self, sql, args): self.calls.append((sql, args))
    def fetchone(self): return self.row


class Connection:
    def __init__(self, cursor): self._cursor = cursor
    def cursor(self): return self._cursor


def authority(**changes):
    now = datetime.now(UTC)
    values = dict(authority_id="opaque-id", operator_identity="operator:dave",
                  surface="web:item", entity_id="SKU-1",
                  goal_profile_id="tgw.ebay_listable", goal_profile_version="1",
                  object_generation="gen", pre_authority_condition_hash="condition",
                  content_identity="content", provider_identity="ebay:account",
                  scopes=("stage",), issued_at=now - timedelta(minutes=1),
                  expires_at=now + timedelta(minutes=10))
    values.update(changes)
    return OperatorAuthority(**values)


def binding():
    return dict(entity_id="SKU-1", goal_profile_id="tgw.ebay_listable",
                goal_profile_version="1", object_generation="gen",
                pre_authority_condition_hash="condition", content_identity="content",
                provider_identity="ebay:account")


def test_issue_uses_server_opaque_id_and_exact_immutable_insert():
    cursor = Cursor()
    now = datetime.now(UTC)
    authority_id = issue_authority(
        operator_identity="operator:dave", surface="web:item", entity_id="SKU-1",
        goal_profile_id="tgw.ebay_listable", goal_profile_version="1",
        object_generation="gen", pre_authority_condition_hash="condition",
        content_identity="content", provider_identity="ebay:account",
        scopes=("stage",), issued_at=now, expires_at=now + timedelta(minutes=5),
        connection=Connection(cursor),
    )
    assert len(authority_id) == 36
    assert "INSERT INTO operator_authorities" in cursor.calls[0][0]
    assert cursor.calls[0][1][0] == authority_id


def test_lookup_validation_rejects_binding_scope_expiry_and_supersession():
    exact = authority()
    assert validate_authority("opaque-id", scope="stage", lookup=lambda _: exact,
                              **binding())[0] == exact
    assert validate_authority("opaque-id", scope="publish", lookup=lambda _: exact,
                              **binding())[0] is None
    wrong = {**binding(), "content_identity": "changed"}
    assert validate_authority("opaque-id", scope="stage", lookup=lambda _: exact,
                              **wrong)[0] is None
    assert validate_authority("opaque-id", scope="stage",
                              lookup=lambda _: authority(superseded_at=datetime.now(UTC)),
                              **binding())[0] is None
    assert validate_authority("opaque-id", scope="stage",
                              lookup=lambda _: authority(expires_at=datetime.now(UTC)),
                              **binding())[0] is None


def test_provider_worker_uses_same_authoritative_validation_seam():
    record = authority()
    assert validate_provider_effect_authority(
        "opaque-id", scope="stage", binding=binding(), lookup=lambda _: record,
    )[0] == record


def test_supersede_is_atomic_and_only_succeeds_once():
    cursor = Cursor(rowcount=1)
    assert supersede_authority("opaque-id", superseded_by="replacement",
                               connection=Connection(cursor)) is True
    assert "superseded_at IS NULL" in cursor.calls[0][0]


def test_malformed_authority_id_is_rejected_before_database_lookup():
    cursor = Cursor()
    assert get_authority("not-a-uuid", connection=Connection(cursor)) is None
    assert cursor.calls == []
