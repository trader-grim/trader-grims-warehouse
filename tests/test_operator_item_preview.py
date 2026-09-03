from __future__ import annotations

import base64
import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

pytest.importorskip("httpx", reason="httpx is required by FastAPI TestClient")
from fastapi.testclient import TestClient  # noqa: E402

_SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "operator_item_preview.py"
_SPEC = importlib.util.spec_from_file_location("operator_item_preview", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
preview = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(preview)

SKU = "tgw202606021107459"
CATEGORY_ID = "123"
PREVIEW_SECRET = "dedicated-preview-test-secret"


@dataclass(frozen=True)
class PreviewTree:
    source_root: Path
    data_root: Path
    manifest_path: Path
    item_path: Path
    context_path: Path


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def preview_tree(tmp_path: Path) -> PreviewTree:
    source_root = tmp_path / "source"
    html_path = source_root / "src/tgw/static/operator_item.html"
    html_path.parent.mkdir(parents=True)
    html_path.write_text("<!doctype html><html><body><main id='app'></main></body></html>")

    data_root = tmp_path / "snapshots"
    item_path = data_root / "ItemData" / SKU / f"{SKU}.json"
    _write_json(
        item_path,
        {
            "sku": SKU,
            "title": "Startup title",
            "location": "A1-01",
            "qty": 1,
            "status": "In Stock",
            "draft_listing": {
                "title": "Startup listing title",
                "description": "Snapshot description",
                "category_id": CATEGORY_ID,
                "category_name": "Startup category",
                "condition_enum": "USED_GOOD",
                "condition_label": "Used - Good",
                "price": 24.5,
                "quantity": 1,
                "item_specifics": {},
            },
        },
    )
    context_path = data_root / f"category-context-{CATEGORY_ID}.json"
    _write_json(
        context_path,
        {
            "category_name": "Startup category",
            "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}],
            "aspects": [],
        },
    )
    manifest_path = data_root / "media-manifest.json"
    _write_json(
        manifest_path,
        {
            "source_base_url": "https://media.preview.test/items",
            "items": {SKU: ["front view.jpg", "detail.jpg"]},
        },
    )
    return PreviewTree(
        source_root=source_root,
        data_root=data_root,
        manifest_path=manifest_path,
        item_path=item_path,
        context_path=context_path,
    )


def _app(tree: PreviewTree, **overrides):
    values = {
        "source_root": tree.source_root,
        "data_root": tree.data_root,
        "media_manifest": tree.manifest_path,
    }
    values.update(overrides)
    return preview.create_app(**values)


def _basic(secret: str, *, username: str = "preview") -> dict[str, str]:
    token = base64.b64encode(f"{username}:{secret}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_non_loopback_requires_dedicated_preview_auth_and_ignores_production_key(
    preview_tree: PreviewTree,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TGW_API_KEY", "production-key-must-not-authorize-preview")
    with pytest.raises(ValueError, match="dedicated preview secret"):
        _app(preview_tree, bind_host="0.0.0.0")

    client = TestClient(
        _app(
            preview_tree,
            bind_host="0.0.0.0",
            preview_secret=PREVIEW_SECRET,
        )
    )
    unauthenticated = client.get("/health")
    wrong_username = client.get(
        "/health",
        headers=_basic(PREVIEW_SECRET, username="operator"),
    )
    wrong_secret = client.get("/health", headers=_basic("wrong-preview-secret"))
    authenticated = client.get("/health", headers=_basic(PREVIEW_SECRET))

    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["www-authenticate"] == 'Basic realm="TGW operator preview"'
    assert wrong_username.status_code == 401
    assert wrong_secret.status_code == 401
    assert authenticated.status_code == 200
    assert authenticated.json()["host_role"] == "tgw-lib-development-preview"


def test_authenticated_post_is_rejected_and_every_command_remains_held(
    preview_tree: PreviewTree,
) -> None:
    client = TestClient(
        _app(
            preview_tree,
            bind_host="tgw-lib",
            preview_secret=PREVIEW_SECRET,
        )
    )
    headers = _basic(PREVIEW_SECRET)

    response = client.post(
        f"/api/operator/items/{SKU}/commands",
        headers=headers,
        json={"command_id": "list-item", "object_generation": "forged", "values": {}},
    )
    published = client.get(f"/api/operator/items/{SKU}", headers=headers)

    assert response.status_code == 405
    assert response.headers["allow"] == "GET, HEAD"
    assert response.json()["code"] == "development_preview_read_only"
    assert published.status_code == 200
    assert published.json()["object"]["commands"]
    assert all(
        command["enabled"] is False
        for command in published.json()["object"]["commands"]
    )


def test_snapshot_is_immutable_after_app_startup(preview_tree: PreviewTree) -> None:
    client = TestClient(_app(preview_tree))
    before = client.get(f"/api/operator/items/{SKU}").json()["object"]

    _write_json(
        preview_tree.item_path,
        {
            "sku": SKU,
            "title": "Changed on disk",
            "draft_listing": {"category_id": CATEGORY_ID},
        },
    )
    _write_json(
        preview_tree.context_path,
        {"category_name": "Changed category", "conditions": [], "aspects": []},
    )
    _write_json(
        preview_tree.manifest_path,
        {
            "source_base_url": "https://changed.invalid",
            "items": {SKU: ["changed.jpg"]},
        },
    )

    after = client.get(f"/api/operator/items/{SKU}").json()["object"]
    category = client.get(f"/api/ebay/category-context/{CATEGORY_ID}").json()

    assert before == after
    assert after["item"]["record"]["title"] == "Startup title"
    assert [entry["name"] for entry in after["item"]["media"]] == [
        "front view.jpg",
        "detail.jpg",
    ]
    assert category["category_name"] == "Startup category"


def test_rejects_symlinked_snapshot_file(preview_tree: PreviewTree, tmp_path: Path) -> None:
    outside = tmp_path / "outside-item.json"
    outside.write_text(preview_tree.item_path.read_text(encoding="utf-8"), encoding="utf-8")
    preview_tree.item_path.unlink()
    preview_tree.item_path.symlink_to(outside)

    with pytest.raises(ValueError, match="must not be a symlink"):
        _app(preview_tree)


def test_rejects_regular_file_resolved_outside_snapshot_root(
    preview_tree: PreviewTree,
    tmp_path: Path,
) -> None:
    outside_manifest = tmp_path / "outside-media-manifest.json"
    outside_manifest.write_text(
        preview_tree.manifest_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="outside its configured root"):
        _app(preview_tree, media_manifest=outside_manifest)


def test_rejects_non_regular_snapshot_resource(preview_tree: PreviewTree) -> None:
    preview_tree.context_path.unlink()
    preview_tree.context_path.mkdir()

    with pytest.raises(ValueError, match="not a regular file"):
        _app(preview_tree)


@pytest.mark.parametrize(
    "name",
    [
        "../outside.jpg",
        "nested/front.jpg",
        r"nested\front.jpg",
        "front..jpg",
        "front.jpg?size=large",
        "front.jpg#main",
        "%2e%2e%2foutside.jpg",
    ],
)
def test_rejects_malformed_media_names(preview_tree: PreviewTree, name: str) -> None:
    _write_json(
        preview_tree.manifest_path,
        {
            "source_base_url": "https://media.preview.test/items",
            "items": {SKU: [name]},
        },
    )

    with pytest.raises(ValueError, match="malformed preview media name"):
        _app(preview_tree)
