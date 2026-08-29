import json
from pathlib import Path

import pytest
import requests

from tgw.ebay import upload


def _response(status=201, *, location='/commerce/media/v1_beta/image/img-1',
              body=None):
    body = body if body is not None else {
        'imageUrl': 'https://i.ebayimg.com/images/g/x/s-l1600.jpg',
        'expirationDate': '2026-10-01T00:00:00Z',
    }
    response = requests.Response()
    response.status_code = status
    response.headers['Location'] = location
    response._content = json.dumps(body).encode()
    response.url = 'https://api.ebay.com/commerce/media/v1_beta/image'
    return response


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr(upload, 'load_token', lambda cfg: 'token')
    monkeypatch.setattr(upload.quota, 'precheck', lambda *a: None)
    monkeypatch.setattr(upload.quota, 'record', lambda *a: None)
    monkeypatch.setattr(upload.quota, 'record_429', lambda *a: None)
    monkeypatch.setattr(upload, 'capture_response', lambda *a: None)


def test_file_contract_metadata_and_exact_receipt(tmp_path, monkeypatch, adapter):
    photo = tmp_path / 'x.jpg'
    # Avoid Pillow decoding here; preparation/resize has its own image tests.
    photo.write_bytes(b'jpeg-bytes')
    monkeypatch.setattr(upload, '_validate_and_derive',
                        lambda path: (b'original', b'derived'))
    captured = {}
    monkeypatch.setattr(upload.requests, 'post',
                        lambda url, **kw: (captured.update(url=url, kwargs=kw)
                                           or _response()))

    prepared = upload.prepare_upload({}, photo, order=2,
                                     attempt_identity='effect-1')
    result = upload.upload_prepared({}, prepared)

    assert str(result).startswith('https://i.ebayimg.com/')
    assert captured['url'].endswith('/v1_beta/image/create_image_from_file')
    assert captured['kwargs']['files']['image'][1] == b'derived'
    assert result.metadata['image_id'] == 'img-1'
    assert result.metadata['expiration_date'] == '2026-10-01T00:00:00Z'
    assert result.metadata['method'] == 'createImageFromFile'
    assert result.metadata['order'] == 2
    assert result.metadata['attempt_identity'] == 'effect-1'
    receipt = result.metadata['receipt']
    assert receipt['request']['source_sha256'] == prepared.source_sha256
    assert receipt['response']['status'] == 201
    assert receipt['response']['body_utf8']


def test_url_adapter_requires_controlled_https_origin(monkeypatch, adapter):
    called = []
    monkeypatch.setattr(upload.requests, 'post',
                        lambda *a, **k: (called.append((a, k)) or _response()))
    cfg = {'ebay_media_controlled_https_origins': ['https://media.tgw.example']}

    result = upload.create_image_from_url(
        cfg, 'https://media.tgw.example/objects/hash.jpg', order=0)
    assert result.metadata['method'] == 'createImageFromUrl'
    assert called[0][1]['json'] == {
        'imageUrl': 'https://media.tgw.example/objects/hash.jpg'}

    for invalid in ('http://media.tgw.example/x.jpg',
                    'https://drive.google.com/file/d/x',
                    'https://user:pass@media.tgw.example/x.jpg'):
        with pytest.raises(upload.UploadDefinitivelyRejected):
            upload.create_image_from_url(cfg, invalid)


@pytest.mark.parametrize('status,exception', [
    (429, upload.UploadQuotaExceeded),
    (415, upload.UploadDefinitivelyRejected),
    (503, requests.HTTPError),
])
def test_failure_classification(monkeypatch, adapter, status, exception):
    monkeypatch.setattr(upload.requests, 'post',
                        lambda *a, **k: _response(status, body={
                            'errors': [{'errorId': 190203, 'message': 'bad image'}]}))
    with pytest.raises(exception):
        upload._dispatch({}, method='createImageFromFile', path='/x',
                         request_receipt={'source_sha256': 'a'})


def test_malformed_success_is_ambiguous_not_definite_rejection(monkeypatch, adapter):
    monkeypatch.setattr(upload.requests, 'post',
                        lambda *a, **k: _response(body={'expirationDate': 'soon'}))
    with pytest.raises(RuntimeError, match='missing Location image identifier or imageUrl'):
        upload._dispatch({}, method='createImageFromFile', path='/x',
                         request_receipt={})


def test_unsupported_media_rejected_before_network(tmp_path, monkeypatch, adapter):
    photo = tmp_path / 'x.psd'
    photo.write_bytes(b'not-supported')
    called = []
    monkeypatch.setattr(upload.requests, 'post', lambda *a, **k: called.append(1))
    with pytest.raises(upload.UploadDefinitivelyRejected, match='unsupported'):
        upload.prepare_upload({}, photo)
    assert called == []


def test_legacy_trading_call_absent():
    source = Path(upload.__file__).read_text(encoding='utf-8')
    forbidden = 'Upload' + 'SiteHostedPictures'
    assert forbidden not in source
    assert 'ws/api.dll' not in source
