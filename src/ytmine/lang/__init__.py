"""Language registry. Add new languages here."""

from __future__ import annotations

from ytmine.lang.base import ClozeParts, Language
from ytmine.lang.zh import Chinese

_REGISTRY: dict[str, type[Language]] = {Chinese.code: Chinese}
_INSTANCES: dict[str, Language] = {}

__all__ = ["ClozeParts", "Language", "get_language"]


def get_language(code: str = "zh") -> Language:
    """Return the (cached) language module for `code`."""
    if code not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY))
        raise ValueError(f"Unsupported language '{code}'. Available: {known}")
    if code not in _INSTANCES:
        _INSTANCES[code] = _REGISTRY[code]()
    return _INSTANCES[code]
