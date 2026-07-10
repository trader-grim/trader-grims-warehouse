"""PP-PHOTOSYNC-001 P7 — ops-digest reads the nightly catalog-verify sidecar.

Scoped narrowly to the addition: ops_digest must never re-run a full catalog
scan itself (expensive, 55k items) — it only reads the JSON sidecar the
nightly timer writes, and degrades cleanly when the sidecar is missing/stale.
"""

import json
from datetime import datetime, timedelta, timezone

from tgw.ops_digest import _catalog_verify_summary, render_text


def _cfg(tmp_path, sidecar_path=None):
    return {'raw': {'catalog_verify_sidecar_path': str(sidecar_path)} if sidecar_path else {}}


def test_missing_sidecar_returns_none(tmp_path):
    cfg = _cfg(tmp_path, tmp_path / 'nope.json')
    assert _catalog_verify_summary(cfg) is None


def test_reads_sidecar_and_computes_age(tmp_path):
    sidecar = tmp_path / 'catalog-verify-nightly.json'
    gen = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    sidecar.write_text(json.dumps({
        'violations': 3, 'by_rule': {'photos_short_on_ebay': 2, 'no_title': 1},
        'generated_at': gen,
    }), encoding='utf-8')
    result = _catalog_verify_summary(_cfg(tmp_path, sidecar))
    assert result['violations'] == 3
    assert result['by_rule']['photos_short_on_ebay'] == 2
    assert 4.9 <= result['age_hours'] <= 5.1


def test_malformed_sidecar_degrades_to_none(tmp_path):
    sidecar = tmp_path / 'bad.json'
    sidecar.write_text('not json', encoding='utf-8')
    assert _catalog_verify_summary(_cfg(tmp_path, sidecar)) is None


def test_render_shows_clean_when_no_violations():
    digest = _make_digest(catalog_verify={'violations': 0, 'by_rule': {}, 'age_hours': 2.0})
    text = render_text(digest)
    assert 'CATALOG-VERIFY — clean' in text


def test_render_shows_violations_and_flags_stale():
    digest = _make_digest(catalog_verify={
        'violations': 5, 'by_rule': {'photos_short_on_ebay': 5}, 'age_hours': 48.0})
    text = render_text(digest)
    assert 'CATALOG-VERIFY — 5 critical violation(s)' in text
    assert 'STALE' in text
    assert 'photos_short_on_ebay' in text


def test_render_omits_section_when_sidecar_absent():
    digest = _make_digest(catalog_verify=None)
    text = render_text(digest)
    assert 'CATALOG-VERIFY' not in text


def _make_digest(catalog_verify):
    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'previous_run': None,
        'health_ok': True,
        'checks_flagged': [],
        'queues': {},
        'dead_letters': {},
        'dead_letter_delta': {},
        'restarts': {},
        'restart_flags': {},
        'quota': {'incidents_today': 0, 'pools': {}},
        'oldest_inbox_note': None,
        'catalog_verify': catalog_verify,
    }
