"""todo #1398 / PP-DEADLETTER-001 — ebay_upload dead-letters on oversized
photo dimensions ("File dimension limit exceeds 15000 pixels.").

`upload_photo()` now runs a pre-flight dimension check before POSTing:
images within eBay's 15000px limit go through byte-identical (no
re-encoding), oversized images get downscaled on a temporary in-memory
copy only — the stored original on disk is never touched.
"""

import io

import pytest

from tgw.ebay import upload as upload_mod
from tgw.ebay.upload import _MAX_DIMENSION_PX, PhotoResizeError, _prepare_upload_bytes

pytest.importorskip('PIL')


def _make_jpeg_bytes(size, color=(200, 50, 50)):
    from PIL import Image
    img = Image.new('RGB', size, color=color)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=90)
    return buf.getvalue()


def test_oversized_image_gets_resized_within_limit(tmp_path):
    """An image over 15000px on one dimension must be downscaled before the
    bytes handed back are within the limit, preserving aspect ratio."""
    oversized = tmp_path / 'huge.jpg'
    # Keep actual pixel generation cheap: use a tall/thin image so total
    # pixel count stays small while one dimension still exceeds the limit.
    oversized.write_bytes(_make_jpeg_bytes((20, 15005)))

    result_bytes = _prepare_upload_bytes(oversized)

    from PIL import Image
    with Image.open(io.BytesIO(result_bytes)) as img:
        w, h = img.size
    assert w <= _MAX_DIMENSION_PX
    assert h <= _MAX_DIMENSION_PX
    # Aspect ratio preserved (within integer-rounding tolerance).
    assert abs((w / h) - (20 / 15005)) < 0.001


def test_normal_image_is_byte_identical_no_reencode(tmp_path):
    """A normal-sized image must be sent unchanged — no unnecessary
    re-encoding/quality loss for the common case."""
    normal = tmp_path / 'normal.jpg'
    raw = _make_jpeg_bytes((800, 600))
    normal.write_bytes(raw)

    result_bytes = _prepare_upload_bytes(normal)

    assert result_bytes == raw


def test_original_file_on_disk_untouched(tmp_path):
    """The stored original must never be mutated by the resize step (Prime
    Directive 1) — even after processing an oversized photo."""
    oversized = tmp_path / 'huge.jpg'
    oversized.write_bytes(_make_jpeg_bytes((15, 15010)))

    _prepare_upload_bytes(oversized)

    from PIL import Image
    with Image.open(oversized) as img:
        w, h = img.size
    assert (w, h) == (15, 15010), 'original file on disk must be unchanged'


def test_corrupt_image_raises_distinct_resize_error(tmp_path, monkeypatch):
    """If the resize itself fails (corrupt/unreadable image pretending to be
    oversized), it must surface as PhotoResizeError, not get masked as a
    plain upload failure (packet spec item 3)."""
    fake = tmp_path / 'corrupt.jpg'
    fake.write_bytes(b'not-a-real-image-but-claims-to-be-huge')

    class _FakeImg:
        size = (99999, 100)
        format = 'JPEG'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def resize(self, *a, **k):
            raise OSError('simulated corrupt image decode failure')

    from PIL import Image
    monkeypatch.setattr(Image, 'open', lambda *a, **k: _FakeImg())

    with pytest.raises(PhotoResizeError):
        _prepare_upload_bytes(fake)


def test_upload_photo_sends_resized_bytes(tmp_path, monkeypatch):
    """End-to-end through upload_photo(): the (mocked) HTTP call receives
    the resized bytes, not the raw oversized original."""
    oversized = tmp_path / 'huge.jpg'
    oversized.write_bytes(_make_jpeg_bytes((10, 15020)))

    monkeypatch.setattr(upload_mod, 'load_token', lambda cfg: 'fake-token')
    monkeypatch.setattr(upload_mod.quota, 'precheck', lambda cfg, api: None)
    monkeypatch.setattr(upload_mod.quota, 'record', lambda cfg, api: None)
    monkeypatch.setattr(upload_mod, 'capture_response', lambda *a, **k: None)

    captured = {}

    class _FakeResp:
        status_code = 200
        content = b''
        text = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<UploadSiteHostedPicturesResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
            '<Ack>Success</Ack>'
            '<SiteHostedPictureDetails><FullURL>https://i.ebayimg.com/x.jpg</FullURL>'
            '</SiteHostedPictureDetails>'
            '</UploadSiteHostedPicturesResponse>'
        )

        def raise_for_status(self):
            return None

    def _fake_post(url, headers=None, files=None, timeout=None):
        captured['files'] = files
        return _FakeResp()

    monkeypatch.setattr(upload_mod.requests, 'post', _fake_post)

    url = upload_mod.upload_photo({}, oversized)

    assert url == 'https://i.ebayimg.com/x.jpg'
    sent_bytes = captured['files']['image'][1]
    assert sent_bytes != oversized.read_bytes(), 'must send resized bytes, not the raw oversized original'
    from PIL import Image
    with Image.open(io.BytesIO(sent_bytes)) as img:
        w, h = img.size
    assert w <= _MAX_DIMENSION_PX and h <= _MAX_DIMENSION_PX
