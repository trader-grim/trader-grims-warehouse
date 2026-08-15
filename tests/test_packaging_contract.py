"""Distribution-package assets required by the installed HTTP service."""

import tomllib
from pathlib import Path


def test_http_static_assets_are_declared_as_package_data():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    package_data = project["tool"]["setuptools"]["package-data"]
    assert "static/*" in package_data["tgw"]
    for name in ("nav.css", "nav.js", "plan_console.html", "tgw.css", "tgw.js"):
        assert (root / "src" / "tgw" / "static" / name).is_file()
