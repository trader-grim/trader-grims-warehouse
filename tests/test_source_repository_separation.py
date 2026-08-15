from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_application_publisher_is_fixed_to_application_repository() -> None:
    wrapper = (ROOT / "scripts" / "tgw-source-git").read_text()

    assert "git@github-tgw-app:trader-grim/trader-grims-warehouse.git" in wrapper
    assert "trader-grim/tgw-flake.git" not in wrapper
    assert "$CANDIDATE:pyproject.toml" in wrapper
    assert "$CANDIDATE:src/tgw/__init__.py" in wrapper
    assert "$CANDIDATE:nix/hosts/tgw-prod.nix" in wrapper
    assert "refusing production-flake history" in wrapper
    assert "repair/application-clean-v1" in wrapper
    assert "dry-run-candidate" in wrapper
    assert "publish-candidate" in wrapper
    assert "refs/heads/main:refs/heads/main" not in wrapper


def test_installer_is_fixed_to_application_repository() -> None:
    installer = (ROOT / "scripts" / "install-tgw-source-access").read_text()

    assert "git@github-tgw-app:trader-grim/trader-grims-warehouse.git" in installer
    assert "trader-grim/tgw-flake.git" not in installer
    sudoers = (ROOT / "config/environment/sudoers/tgw-source-git").read_text()
    assert "publish-candidate" in sudoers
    assert " tgw-source-git publish\n" not in sudoers


def test_successor_runbooks_state_two_repository_boundary() -> None:
    separation = (ROOT / "docs/runbooks/repository-separation-v1-20260815.md").read_text()
    access = (ROOT / "docs/runbooks/shared-source-access-v2-20260815.md").read_text()

    assert "trader-grim/trader-grims-warehouse" in separation
    assert "trader-grim/tgw-flake" in separation
    assert "two distinct GitHub repositories" in separation
    assert "not registered with GitHub" in access
