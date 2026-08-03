"""The backend translation files, checked against each other.

docs/14 §14.6.2 names hassfest as the test for these files, and it is: hassfest validates
the mirror structure and the brace rule in CI, and no unit test duplicates that.

**This module is an addition to the spec, and it is here for one reason:** hassfest runs
against a pull request, and a key that goes missing from `es.json` during an unrelated
edit is exactly the kind of drift that is cheap to catch here and expensive to notice on a
Spanish instance, where it shows as a config-flow field with an English label and no
error anywhere. Two assertions, no fixtures, milliseconds.

The brace assertion is the one trap this project has already paid for (commit `5e0073b`):
hassfest reads `{}` in a translation string as placeholder syntax and fails the build, so
the JSON-shaped examples in the service descriptions had to be rewritten as prose. The ES
file was written prose-first for the same reason; this keeps it that way.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

TRANSLATIONS = (
    Path(__file__).resolve().parents[2] / "custom_components" / "filament_ledger" / "translations"
)


def load(name: str) -> object:
    """The file as parsed JSON. `object` on purpose: the walkers below narrow it, and
    claiming a shape here would assert the very thing the tests exist to check."""
    parsed: object = json.loads((TRANSLATIONS / name).read_text(encoding="utf-8"))
    return parsed


def paths(node: object, prefix: str = "") -> Iterator[str]:
    """Every leaf's dotted path — the shape hassfest compares languages by."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from paths(value, f"{prefix}.{key}" if prefix else key)
    else:
        yield prefix


def strings(node: object, prefix: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from strings(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, str):
        yield prefix, node


LANGUAGES = sorted(path.name for path in TRANSLATIONS.glob("*.json"))


def test_both_languages_are_present() -> None:
    """Guards the parity test below from passing because a file went missing."""
    assert LANGUAGES == ["en.json", "es.json"]


def test_es_mirrors_en_key_for_key() -> None:
    """Structure must match exactly, in both directions.

    A key only in EN is a Spanish instance falling back to English silently; a key only in
    ES is a translation of something that no longer exists, which reads as coverage and
    is not.
    """
    english = set(paths(load("en.json")))
    spanish = set(paths(load("es.json")))

    assert sorted(english - spanish) == [], "keys missing from es.json"
    assert sorted(spanish - english) == [], "keys in es.json that en.json does not have"


@pytest.mark.parametrize("language", LANGUAGES)
def test_no_translation_string_contains_a_literal_brace(language: str) -> None:
    """Commit `5e0073b`, as a test. hassfest reads a brace as placeholder syntax."""
    offenders = [key for key, text in strings(load(language)) if "{" in text or "}" in text]

    assert offenders == [], f"{language} has braces in {offenders}; write prose instead"


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_string_is_non_empty(language: str) -> None:
    """A blank translation is worse than a missing one: hassfest accepts it, and the
    user sees an unlabelled field rather than an English one."""
    offenders = [key for key, text in strings(load(language)) if not text.strip()]

    assert offenders == []
