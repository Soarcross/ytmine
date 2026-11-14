"""Shared test helpers."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_text():
    def read(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    return read


@pytest.fixture(autouse=True)
def no_network_dictionary(monkeypatch):
    """Tests never download CC-CEDICT. Tests that need a dictionary build one from a fixture."""
    from ytmine.lang import cedict

    def offline():
        raise OSError("network disabled in tests")

    monkeypatch.setattr(cedict, "download_cedict", offline)
