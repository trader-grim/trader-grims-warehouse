"""Provider-effect environment isolation for eBay production and sandbox."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

import tgw.provider_effects as provider_effects
from tgw.apis.ebay import get_access_token, refresh_access_token
from tgw.item_mutation import item_generation
from tgw.workers import ebay_publish, ebay_stage, token_refresh


# NOTE (reconciliation, Todo 1961): the eBay Picture Service (EPS) upload-path
# environment tests that lived here were removed.  Concept Registry v0.1 row 2
# resolves src/tgw/ebay/upload.py and src/tgw/workers/ebay_upload.py to main's
# Media API upload shape; the branch's prepare_upload/upload_prepared EPS
# contract (PreparedUpload.environment/.endpoint, UploadEnvironmentMismatch,
# worker payload environment-drift guard) is not part of that resolution.
# Token-refresh, OAuth, provider-effect and disk/process cache environment
# isolation below remain in force.


def _bound_refresh_config(tmp_path, environment='sandbox'):
    secrets = tmp_path / 'alternate-secrets-root'
    secrets.mkdir()
    credentials = secrets / 'ebay-credentials.json'
    credentials.write_text(json.dumps({
        'app_id': 'PROD-APP', 'cert_id': 'PROD-CERT',
        'sandbox_app_id': 'SBX-APP', 'sandbox_cert_id': 'SBX-CERT',
    }), encoding='utf-8')
    token_name = 'ebay-sandbox-token.json' if environment == 'sandbox' else 'ebay-token.json'
    token_path = secrets / token_name
    token_path.write_text(json.dumps({
        'access_token': 'old', 'refresh_token': f'{environment}-refresh', 'expiry': 0,
        '_tgw_ebay_environment': environment,
    }), encoding='utf-8')
    return {
        'ebay_environment': environment,
        'secrets_root': secrets,
        'ebay_credentials_path': credentials,
        'ebay_token_path': token_path,
        'raw': {
            'ebay_environment': environment,
            'ebay': {'oauth': {
                environment: {
                    'ru_name': f'{environment}-RuName',
                    'scopes': f'{environment}-scope',
                },
            }},
        },
    }


def test_refresh_uses_exact_nondefault_bound_config_token_credentials_and_scope(
    tmp_path, monkeypatch,
):
    cfg = _bound_refresh_config(tmp_path)
    calls = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {'access_token': 'new-sandbox', 'expires_in': 7200}

    monkeypatch.setattr(
        refresh_access_token.requests, 'post',
        lambda url, **kwargs: calls.append((url, kwargs)) or _Response(),
    )

    assert refresh_access_token.refresh_access_token(
        force=True, config=cfg,
    ) == 'new-sandbox'
    url, kwargs = calls[0]
    assert url == 'https://api.sandbox.ebay.com/identity/v1/oauth2/token'
    encoded = kwargs['headers']['Authorization'].removeprefix('Basic ')
    assert base64.b64decode(encoded).decode() == 'SBX-APP:SBX-CERT'
    assert kwargs['data']['scope'] == 'sandbox-scope'
    assert json.loads(cfg['ebay_token_path'].read_text())['access_token'] == 'new-sandbox'


def test_token_refresh_worker_passes_its_exact_config(monkeypatch, tmp_path):
    cfg = _bound_refresh_config(tmp_path, 'production')
    worker = token_refresh.TokenRefreshWorker.__new__(token_refresh.TokenRefreshWorker)
    worker.config = cfg
    seen = []
    monkeypatch.setattr(
        refresh_access_token, 'refresh_access_token',
        lambda **kwargs: seen.append(kwargs) or 'new-token',
    )
    monkeypatch.setattr(worker, '_reschedule', lambda: None)
    monkeypatch.setattr(token_refresh, 'notify', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(token_refresh.tgw_logging, 'log_event', lambda *_args, **_kwargs: None)

    worker.handle({'payload_json': {}})

    assert seen == [{'force': True, 'config': cfg}]


def test_oauth_runame_redirect_and_scopes_are_selected_per_environment(
    tmp_path, monkeypatch,
):
    secrets = tmp_path / 'secrets'
    secrets.mkdir()
    (secrets / 'ebay-credentials.json').write_text(json.dumps({
        'app_id': 'PROD-APP', 'cert_id': 'PROD-CERT',
        'sandbox_app_id': 'SBX-APP', 'sandbox_cert_id': 'SBX-CERT',
    }), encoding='utf-8')
    raw = {
        'secrets_root': str(secrets),
        'ebay': {'oauth': {
            'production': {'ru_name': 'PROD-RUNAME', 'scopes': 'prod-scope'},
            'sandbox': {'ru_name': 'SBX-RUNAME', 'scopes': 'sandbox-scope'},
        }},
    }
    monkeypatch.setattr(get_access_token, '_load_raw_config', lambda: raw)
    monkeypatch.setattr(get_access_token, '_secrets_root', lambda: secrets)

    production = get_access_token.load_config(is_sandbox=False)
    sandbox = get_access_token.load_config(is_sandbox=True)
    assert (production['redirect_uri'], production['scopes']) == (
        'PROD-RUNAME', 'prod-scope',
    )
    assert (sandbox['redirect_uri'], sandbox['scopes']) == (
        'SBX-RUNAME', 'sandbox-scope',
    )
    sandbox_url = get_access_token.generate_auth_url(sandbox, is_sandbox=True)
    parsed = urlparse(sandbox_url)
    assert parsed.netloc == 'auth.sandbox.ebay.com'
    assert parse_qs(parsed.query)['redirect_uri'] == ['SBX-RUNAME']
    assert parse_qs(parsed.query)['scope'] == ['sandbox-scope']
    with pytest.raises(ValueError, match='differs'):
        get_access_token.generate_auth_url(sandbox, is_sandbox=False)


def _effect(effect_id='effect-1', state='dispatched', result=None):
    return SimpleNamespace(effect_id=effect_id, state=state, result=result)


def _workflow_payload(treatment):
    return {
        'sku': 'SKU-1', 'treatment_id': treatment, 'treatment_version': '1',
        'graph_id': 'graph', 'goal_profile_id': 'goal',
        'goal_profile_version': '1', 'object_generation': 'generation',
        'condition_hash': 'condition', 'operator_authority_id': 'authority',
        'pre_authority_condition_hash': 'pre-condition',
    }


@pytest.mark.parametrize(
    ('worker_type', 'module', 'method_name', 'treatment', 'provider_result'),
    [
        (
            ebay_stage.EbayStageWorker, ebay_stage, '_stage_with_provider_effect',
            'ebay-stage', {'offer_id': 'OFF-1', 'inventory_item': {}},
        ),
        (
            ebay_publish.EbayPublishWorker, ebay_publish, '_publish_with_provider_effect',
            'ebay-publish', {'listing_id': 'LIST-1', 'listing_url': 'https://example'},
        ),
    ],
)
def test_stage_and_publish_effect_requests_and_results_bind_sandbox_environment(
    tmp_path, monkeypatch, worker_type, module, method_name, treatment, provider_result,
):
    data_root = tmp_path / 'data'
    itemdata_root = data_root / 'ItemData'
    item = {'sku': 'SKU-1'}
    item_dir = itemdata_root / item['sku']
    item_dir.mkdir(parents=True)
    (item_dir / f'{item["sku"]}.json').write_text(
        json.dumps(item), encoding='utf-8',
    )
    worker = worker_type.__new__(worker_type)
    worker.config = {
        'data_root': data_root,
        'itemdata_root': itemdata_root,
        'ebay_environment': 'sandbox',
        'workflow_migration': {
            'ebay_stage_provider_effect': 'workflow',
            'ebay_publish_provider_effect': 'workflow',
            'ebay_provider_identity': 'seller-1',
        },
    }
    captured = {}
    monkeypatch.setattr(
        module,
        'validate_listing_condition_for_stage',
        lambda *args, **kwargs: 'USED_GOOD',
    )
    monkeypatch.setattr(
        provider_effects, 'reserve_and_begin_authorized_effect',
        lambda **kwargs: captured.update(kwargs) or _effect(),
    )
    monkeypatch.setattr(
        provider_effects, 'finish_provider_effect',
        lambda effect_id, **kwargs: _effect(effect_id, kwargs['state'], kwargs.get('result')),
    )
    payload = _workflow_payload(treatment)
    payload['object_generation'] = item_generation(item)
    if method_name == '_stage_with_provider_effect':
        monkeypatch.setattr(module, 'stage_draft', lambda *_args: provider_result)
        result, _effect_id, _identity = getattr(worker, method_name)(
            payload, 'SKU-1', item, force=False,
        )
    else:
        monkeypatch.setattr(module, 'publish_offer', lambda *_args: provider_result)
        result = getattr(worker, method_name)(
            payload, 'SKU-1', 'OFF-1', item,
        )

    assert captured['request']['ebay_environment'] == 'sandbox'
    assert captured['request']['endpoint'] == 'https://api.sandbox.ebay.com'
    assert result['ebay_environment'] == 'sandbox'
    assert result['endpoint'] == 'https://api.sandbox.ebay.com'


def test_ebay_disk_and_process_caches_are_environment_namespaced(tmp_path):
    from tgw.apis.ebay import conditions, specifics, taxonomy

    production = {'catalog_root': tmp_path, 'ebay_environment': 'production'}
    sandbox = {'catalog_root': tmp_path, 'ebay_environment': 'sandbox'}
    assert taxonomy._tree_cache_path(production).name == 'ebay-category-tree.json'
    assert taxonomy._tree_cache_path(sandbox).name == 'ebay-sandbox-category-tree.json'
    assert conditions._cache_path(production).name == 'ebay-condition-policies.json'
    assert conditions._cache_path(sandbox).name == 'ebay-sandbox-condition-policies.json'
    assert specifics._aspects_cache_path(production).name == 'ebay-aspects-cache.json'
    assert specifics._aspects_cache_path(sandbox).name == 'ebay-sandbox-aspects-cache.json'
    assert specifics._aspects_cache_key(production, '123') != specifics._aspects_cache_key(
        sandbox, '123'
    )
