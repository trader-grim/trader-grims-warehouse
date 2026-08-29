from __future__ import annotations

import json

from tgw import doctor_cli


def test_doctor_condition_policy_census_is_read_only_and_reports_actual_drift(
    tmp_path, capsys
):
    cache_path = tmp_path / "ebay-condition-policies.json"
    cache_path.write_text(
        json.dumps({
            "policies": {
                "required-a": [["1000", "New"], ["3000", "Used"]],
                "required-b": [["3000", "Pre-owned"], ["1000", "Brand New"]],
                "108857": [],
                "invalid-flag": [["5000", "Good"]],
            },
            "item_condition_required": {
                "required-a": True,
                "required-b": True,
                "108857": False,
                "invalid-flag": "yes",
            },
        }),
        encoding="utf-8",
    )
    before = cache_path.read_bytes()

    assert doctor_cli.main([
        "condition-policy-census",
        "--cache",
        str(cache_path),
    ]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["category_count"] == 4
    assert report["required_flag_coverage"] == 3
    assert report["required_flag_missing_or_invalid"] == 1
    assert report["actual_distinct_condition_id_sets"] == 3
    assert report["expected_distinct_condition_id_sets"] == 26
    assert report["condition_id_sets"] == [[], ["1000", "3000"], ["5000"]]
    assert report["drift"] is True
    assert report["read_only"] is True
    assert "groups" not in report
    assert cache_path.read_bytes() == before


def test_doctor_condition_policy_census_deduplicates_ids_within_each_category(
    tmp_path,
):
    cache_path = tmp_path / "ebay-condition-policies.json"
    cache_path.write_text(
        json.dumps({
            "policies": {
                "single": [["1000", "New"]],
                "duplicate-row": [
                    ["1000", "New"],
                    ["1000", "New duplicate"],
                ],
            },
            "item_condition_required": {
                "single": True,
                "duplicate-row": True,
            },
        }),
        encoding="utf-8",
    )

    report = doctor_cli.condition_policy_census(cache_path)

    assert report["actual_distinct_condition_id_sets"] == 1
    assert report["condition_id_sets"] == [["1000"]]
