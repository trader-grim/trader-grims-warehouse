from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text("utf-8"))


@pytest.fixture
def openrouter_payload():
    return _load("openrouter_models.json")


@pytest.fixture
def groq_payload():
    return _load("groq_models.json")


@pytest.fixture
def models_dev_payload():
    return _load("models_dev.json")
