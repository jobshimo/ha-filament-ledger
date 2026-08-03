"""The translation tables, checked against each other — backend JSON and panel module both.

docs/14 §14.6.2 names hassfest as the test for the backend files, and it is: hassfest
validates the mirror structure and the brace rule in CI, and no unit test duplicates that.
Nothing validates `www/i18n.js`, which is the panel's entire vocabulary, so the second half
of this module does.

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
import re
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


# -- the panel's own string table (www/i18n.js) -----------------------------------------
#
# The panel has no JavaScript harness (ADR-0006: no framework, no bundler, no build step),
# so the rule that every user-facing string lives in `i18n.js` in **both** languages is
# enforced by reading — except for the part a regex can check, which is this part. Parsing
# JavaScript with a pattern is normally a bad idea; it is defensible here because the file
# is a hand-written literal table with one shape, and the alternative is no check at all.

I18N = (
    Path(__file__).resolve().parents[2]
    / "custom_components"
    / "filament_ledger"
    / "www"
    / "i18n.js"
)

_KEY = re.compile(r'^ {2}"([\w.]+)":\s*(.*?)(?=^ {2}"[\w.]+":|\Z)', re.MULTILINE | re.DOTALL)
_TOKEN = re.compile(r"\[\[(\w+)\]\]")


def table(name: str) -> dict[str, str]:
    """One language table as key → its raw source, concatenation and all.

    The value is kept verbatim rather than evaluated: the assertions below ask whether a
    string exists and which `[[tokens]]` it carries, and both questions are answered by the
    source text without this test having to become a JavaScript interpreter.
    """
    source = I18N.read_text(encoding="utf-8")
    start = source.index(f"const {name} = {{")
    return dict(_KEY.findall(source[start : source.index("\n};", start)]))


def test_the_panel_table_has_both_languages() -> None:
    """Guards the parity test below from passing because a table went missing."""
    assert len(table("EN")) > 0
    assert len(table("ES")) > 0


def test_the_panel_tables_mirror_each_other_key_for_key() -> None:
    """A key only in EN is a Spanish panel silently falling back to English — which the
    translator does on purpose, so nothing on screen looks broken and nobody ever notices.
    A key only in ES is a translation of something that no longer exists, which reads as
    coverage and is not."""
    english = set(table("EN"))
    spanish = set(table("ES"))

    assert sorted(english - spanish) == [], "keys missing from the ES table in www/i18n.js"
    assert sorted(spanish - english) == [], "keys in the ES table that EN does not have"


def test_every_panel_string_carries_the_same_placeholders_in_both_languages() -> None:
    """A translation that drops a `[[token]]` loses the number the sentence was about, and
    one that invents a token renders the literal `[[token]]` on screen. Both are silent:
    the substitution leaves an unmatched token standing rather than blanking it, precisely
    so the failure is visible — and this makes it visible in CI instead of on a phone."""
    english = table("EN")
    spanish = table("ES")
    mismatched = {
        key: (sorted(set(_TOKEN.findall(english[key]))), sorted(set(_TOKEN.findall(value))))
        for key, value in spanish.items()
        if key in english and set(_TOKEN.findall(english[key])) != set(_TOKEN.findall(value))
    }

    assert mismatched == {}


def test_no_panel_string_is_blank() -> None:
    """A blank string is worse than a missing one: the fallback never fires, and the user
    sees an unlabelled control rather than an English one."""
    offenders = [
        key
        for language in ("EN", "ES")
        for key, value in table(language).items()
        if not value.strip().strip(",")
    ]

    assert offenders == []
