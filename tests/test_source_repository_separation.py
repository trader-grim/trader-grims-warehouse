from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_application_publisher_is_fixed_to_application_repository() -> None:
    wrapper = (ROOT / "scripts" / "tgw-source-git").read_text()

    assert "git@github-tgw-app:trader-grim/trader-grims-warehouse.git" in wrapper
    assert "trader-grim/tgw-flake.git" not in wrapper
    assert "refs/heads/main:refs/heads/main" in wrapper
    assert "refs/heads/production:refs/heads/production" in wrapper
    assert "refs/heads/integrate/full-plan-fb9:refs/heads/integrate/full-plan-fb9" in wrapper
    assert "verify_local_lineage" in wrapper
    assert "merge-base --is-ancestor production main" in wrapper
    assert "dry-run" in wrapper
    assert "publish" in wrapper
    assert "repair/application-clean-v1" not in wrapper
    assert "candidate" not in wrapper.lower()


def test_installer_is_fixed_to_application_repository() -> None:
    installer = (ROOT / "scripts" / "install-tgw-source-access").read_text()

    assert "git@github-tgw-app:trader-grim/trader-grims-warehouse.git" in installer
    assert "trader-grim/tgw-flake.git" not in installer
    sudoers = (ROOT / "config/environment/sudoers/tgw-source-git").read_text()
    assert "/usr/local/bin/tgw-source-git dry-run" in sudoers
    assert "/usr/local/bin/tgw-source-git publish" in sudoers
    assert "publish-candidate" not in sudoers


def test_successor_runbooks_state_three_repository_boundary() -> None:
    separation = (ROOT / "docs/runbooks/three-repository-boundary-v3-20260815.md").read_text()
    access = (ROOT / "docs/runbooks/shared-source-access-v3-20260815.md").read_text()

    assert "trader-grim/trader-grims-warehouse" in separation
    assert "trader-grim/tgw-flake" in separation
    assert "/opt/TGW/library/plans" in separation
    assert "three source-control authority domains" in separation
    assert "/opt/TGW/tgw-lib/src/trader-grims-warehouse" in separation
    assert "/home/db/tgw-flake" in separation
    assert "tgw-source-git publish" in access


def test_application_repository_contains_no_embedded_plan_authority() -> None:
    assert not (ROOT / "docs" / "TGW-Plan-Vault").exists()
