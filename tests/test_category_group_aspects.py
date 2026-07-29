import json
import logging
from collections import OrderedDict
from unittest.mock import patch

import pytest

import tgw.apis.ebay.taxonomy as taxonomy
import tgw.apis.lookup as lookup
import tgw.image_hash as image_hash
import tgw.quota as quota
from tgw.apis.ebay import specifics
from tgw.workers import ai_identify


def _aspect(name, *, required=False):
    return {
        "name": name,
        "required": required,
        "mode": "FREE_TEXT",
        "allowed_values": [],
    }


def setup_function():
    specifics._aspects_mem_cache.clear()


def test_category_group_union_is_deterministic_and_deduplicated():
    by_category = {
        "200": [_aspect("Color"), _aspect("Model")],
        "100": [_aspect("Brand"), _aspect("Model", required=True)],
    }

    with patch.object(
        specifics,
        "get_aspects",
        side_effect=lambda _cfg, category_id: by_category[category_id],
    ) as get_aspects:
        result = specifics.get_category_group_aspects({}, ["200", "100", "200"])

    assert [aspect["name"] for aspect in result] == ["Brand", "Model", "Color"]
    assert result[1]["required"] is True
    assert [call.args[1] for call in get_aspects.call_args_list] == ["100", "200"]


def test_category_group_union_propagates_failed_taxonomy_lookup():
    with patch.object(
        specifics,
        "get_aspects",
        side_effect=[[_aspect("Brand")], RuntimeError("taxonomy unavailable")],
    ):
        with pytest.raises(RuntimeError, match="taxonomy unavailable"):
            specifics.get_category_group_aspects({}, ["100", "200"])


def test_aspect_cache_is_bounded_and_configuration_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(specifics, "_ASPECTS_MEM_CACHE_MAX", 2)
    cfg_a = {"catalog_root": str(tmp_path / "a")}
    cfg_b = {"catalog_root": str(tmp_path / "b")}
    responses = [
        {"aspects": [{"localizedAspectName": "A"}]},
        {"aspects": [{"localizedAspectName": "B"}]},
        {"aspects": [{"localizedAspectName": "C"}]},
        {"aspects": [{"localizedAspectName": "Other config"}]},
    ]

    with patch.object(specifics, "get_category_tree_id", return_value="0"), patch.object(
        specifics, "ebay_get", side_effect=responses
    ) as ebay_get:
        specifics.get_aspects(cfg_a, "1")
        specifics.get_aspects(cfg_a, "2")
        specifics.get_aspects(cfg_a, "3")
        other = specifics.get_aspects(cfg_b, "3")

    assert len(specifics._aspects_mem_cache) == 2
    assert other[0]["name"] == "Other config"
    assert ebay_get.call_count == 4


def test_rootless_configurations_do_not_share_process_memory_aspects():
    cfg_a = {"ebay_token": "config-a"}
    cfg_b = {"ebay_token": "config-b"}
    responses = [
        {"aspects": [{"localizedAspectName": "Config A"}]},
        {"aspects": [{"localizedAspectName": "Config B"}]},
    ]

    with patch.object(specifics, "get_category_tree_id", return_value="0"), patch.object(
        specifics, "ebay_get", side_effect=responses
    ) as ebay_get:
        first = specifics.get_aspects(cfg_a, "123")
        second = specifics.get_aspects(cfg_b, "123")

    assert first[0]["name"] == "Config A"
    assert second[0]["name"] == "Config B"
    assert ebay_get.call_count == 2
    assert not specifics._aspects_mem_cache


def test_rooted_calls_cache_repeated_category_and_isolate_distinct_roots(
    tmp_path,
):
    cfg_a = {"catalog_root": str(tmp_path / "a")}
    cfg_b = {"catalog_root": str(tmp_path / "b")}
    responses = [
        {"aspects": [{"localizedAspectName": "Config A"}]},
        {"aspects": [{"localizedAspectName": "Config B"}]},
    ]

    with patch.object(specifics, "get_category_tree_id", return_value="0"), patch.object(
        specifics, "ebay_get", side_effect=responses
    ) as ebay_get:
        first = specifics.get_aspects(cfg_a, "123")
        repeated = specifics.get_aspects(cfg_a, "123")
        other = specifics.get_aspects(cfg_b, "123")

    assert repeated is first
    assert other[0]["name"] == "Config B"
    assert ebay_get.call_count == 2


def test_aspect_cache_state_operations_hold_lock(tmp_path, monkeypatch):
    class LockCheckingCache(OrderedDict):
        def _assert_locked(self):
            assert specifics._aspects_mem_cache_lock._is_owned()

        def __contains__(self, key):
            self._assert_locked()
            return super().__contains__(key)

        def __getitem__(self, key):
            self._assert_locked()
            return super().__getitem__(key)

        def __setitem__(self, key, value):
            self._assert_locked()
            return super().__setitem__(key, value)

        def __len__(self):
            self._assert_locked()
            return super().__len__()

        def move_to_end(self, key, last=True):
            self._assert_locked()
            return super().move_to_end(key, last)

        def popitem(self, last=True):
            self._assert_locked()
            return super().popitem(last)

    monkeypatch.setattr(specifics, "_aspects_mem_cache", LockCheckingCache())
    cfg = {"catalog_root": str(tmp_path)}
    response = {"aspects": [{"localizedAspectName": "Brand"}]}

    with patch.object(specifics, "get_category_tree_id", return_value="0"), patch.object(
        specifics, "ebay_get", return_value=response
    ) as ebay_get:
        specifics.get_aspects(cfg, "123")
        specifics.get_aspects(cfg, "123")

    assert ebay_get.call_count == 1


def test_ai_identify_targets_set_a_union_without_changing_set_b(tmp_path):
    groups_path = tmp_path / "category-groups.json"
    groups_path.write_text(
        json.dumps(
            {
                "groups": {
                    "appliances": {
                        "ebay_categories": ["20673", "12345"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    item = {
        "category_group": "appliances",
        "ebay_category_id": "20673",
    }
    union = [_aspect("Brand"), _aspect("Model")]

    with patch.object(
        ai_identify, "get_category_group_aspects", return_value=union
    ) as group_aspects:
        prompt = ai_identify._prompt_for_item(
            item,
            {"category_groups_path": str(groups_path)},
            hint="food processor",
            product_context="",
        )

    group_aspects.assert_called_once_with(
        {"category_groups_path": str(groups_path)}, ["20673", "12345"]
    )
    assert "Set A category-group target aspects" in prompt
    assert "Brand" in prompt
    assert "Model" in prompt
    assert "item_specifics" in prompt

    # Set B remains selected-category taxonomy through the named translator;
    # a Model valid only in another group category must not enter Set B.
    with patch(
        "tgw.ebay.aspect_translation.get_aspects",
        return_value=[_aspect("Brand")],
    ):
        translated = __import__(
            "tgw.ebay.aspect_translation", fromlist=["translate_inventory_to_ebay_draft"]
        ).translate_inventory_to_ebay_draft(
            {"Brand": "Acme", "Model": "EV-11PC9"},
            "20673",
            {},
        )
    assert translated == {"Brand": "Acme"}


def test_ai_identify_degrades_to_freeform_when_group_lookup_fails(tmp_path, caplog):
    groups_path = tmp_path / "category-groups.json"
    groups_path.write_text(
        json.dumps(
            {"groups": {"appliances": {"category_candidates": ["20673", "12345"]}}}
        ),
        encoding="utf-8",
    )

    with patch.object(
        ai_identify,
        "get_category_group_aspects",
        side_effect=RuntimeError("taxonomy unavailable"),
    ):
        prompt = ai_identify._prompt_for_item(
            {"category_group": "appliances"},
            {"category_groups_path": str(groups_path)},
            hint="",
            product_context="",
        )

    assert "item_specifics" in prompt
    assert "Set A category-group target aspects" not in prompt
    assert "taxonomy unavailable" in caplog.text


def test_ai_identify_propagates_quota_from_group_lookup(tmp_path, monkeypatch):
    groups_path = tmp_path / "category-groups.json"
    groups_path.write_text(
        json.dumps(
            {"groups": {"appliances": {"category_candidates": ["20673", "12345"]}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ai_identify,
        "get_category_group_aspects",
        lambda _cfg, _ids: (_ for _ in ()).throw(
            quota.QuotaBudgetExceeded("taxonomy quota exhausted")
        ),
    )

    with pytest.raises(quota.QuotaBudgetExceeded, match="taxonomy quota exhausted"):
        ai_identify._prompt_for_item(
            {"category_group": "appliances"},
            {"category_groups_path": str(groups_path)},
            hint="",
            product_context="",
        )


@pytest.mark.parametrize(
    ("groups", "warning"),
    [
        ({"groups": {}}, "missing metadata"),
        (
            {"groups": {"appliances": {"ebay_categories": []}}},
            "empty category list",
        ),
        (
            {"groups": {"appliances": {"category_candidates": []}}},
            "empty category list",
        ),
    ],
)
def test_ai_identify_warns_and_degrades_for_missing_or_empty_group_metadata(
    tmp_path, caplog, groups, warning
):
    groups_path = tmp_path / "category-groups.json"
    groups_path.write_text(json.dumps(groups), encoding="utf-8")

    with caplog.at_level(logging.WARNING), patch.object(
        ai_identify, "get_category_group_aspects"
    ) as group_aspects:
        prompt = ai_identify._prompt_for_item(
            {"category_group": "appliances"},
            {"category_groups_path": str(groups_path)},
            hint="",
            product_context="",
        )

    assert "item_specifics" in prompt
    assert "Set A category-group target aspects" not in prompt
    assert warning in caplog.text
    group_aspects.assert_not_called()


def test_model_valid_in_any_group_category_is_persisted_in_set_a(
    tmp_path, monkeypatch
):
    sku = "tgw-model-union"
    sku_dir = tmp_path / sku
    sku_dir.mkdir()
    (sku_dir / f"{sku}.json").write_text(
        json.dumps(
            {
                "sku": sku,
                "title": "",
                "ai_identified": False,
                "category_group": "appliances",
            }
        ),
        encoding="utf-8",
    )
    photo = sku_dir / "photo.jpg"
    photo.write_bytes(b"fake")
    groups_path = tmp_path / "category-groups.json"
    groups_path.write_text(
        json.dumps(
            {
                "groups": {
                    "appliances": {
                        "ebay_categories": ["20673", "12345"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    cfg = {
        "itemdata_root": tmp_path,
        "category_groups_path": str(groups_path),
    }
    worker = ai_identify.AIIdentifyWorker.__new__(ai_identify.AIIdentifyWorker)
    worker.config = cfg
    prompts = []
    patched = {}

    monkeypatch.setattr(
        ai_identify, "_asset_ordered_photos", lambda _item, _sku_dir: [photo]
    )
    monkeypatch.setattr(
        ai_identify,
        "get_task_model",
        lambda _cfg, _task: ("openrouter", "test-model"),
    )
    monkeypatch.setattr(
        ai_identify, "_encode_resized", lambda _path, max_px: ("b64", 1, 1)
    )
    monkeypatch.setattr(
        ai_identify,
        "get_category_group_aspects",
        lambda _cfg, _ids: [_aspect("Brand"), _aspect("Model")],
    )
    monkeypatch.setattr(
        ai_identify,
        "call_model",
        lambda _task, _system, prompt, _cfg, **_kwargs: prompts.append(prompt)
        or json.dumps(
            {
                "title": "Acme Food Processor",
                "category": "Food Processors",
                "item_specifics": {"Model": "EV-11PC9"},
            }
        ),
    )
    monkeypatch.setattr(ai_identify, "extract_json", json.loads)
    monkeypatch.setattr(lookup, "lookup_product", lambda _item, _cfg: None)
    monkeypatch.setattr(
        taxonomy,
        "best_category",
        lambda _cfg, _title, _category: ("20673", "Food Processors"),
    )
    monkeypatch.setattr(image_hash, "compute_dhash", lambda _path: "hash")
    monkeypatch.setattr(image_hash, "lookup_hash", lambda _key, _task: None)
    monkeypatch.setattr(image_hash, "store_hash", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        ai_identify.state_machine, "enqueue_job", lambda **_kwargs: "job"
    )
    monkeypatch.setattr(
        ai_identify,
        "fence_patch_item",
        lambda _cfg, _sku, fields: patched.update(fields) or {"ok": True},
    )

    worker.handle({"payload_json": {"sku": sku, "catalog_only": True}})

    assert "Model" in prompts[0]
    assert patched["ebay_category_id"] == "20673"
    assert patched["item_attributes"]["fields"]["Model"] == "EV-11PC9"
    assert patched["item_attributes"]["_set"] == "inventory_record"
