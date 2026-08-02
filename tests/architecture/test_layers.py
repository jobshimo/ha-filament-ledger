"""The architecture, as executable rules.

An architectural rule that lives only in a document is a rule that will be broken during the
first difficult afternoon. A rule with a failing test is a rule that holds.

These are the executable form of docs/03-architecture.md §3.2 and docs/adr/0005-async-io-ports.md.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Iterator
from pathlib import Path

import pytest

from custom_components.filament_ledger.domain.port.repositories import MovementRepository

INTEGRATION = Path(__file__).resolve().parents[2] / "custom_components" / "filament_ledger"
DOMAIN = INTEGRATION / "domain"
APPLICATION = INTEGRATION / "application"

FORBIDDEN_INWARD = ("homeassistant", "sqlite3", "aiohttp", "voluptuous", "custom_components")

MUTATION_WORDS = ("update", "delete", "remove", "modify", "edit", "set_", "overwrite")


def modules_under(root: Path) -> Iterator[Path]:
    if not root.exists():
        return
    yield from sorted(root.rglob("*.py"))


def imported_names(path: Path) -> set[str]:
    """Every top-level module name this file imports, absolute imports only."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def relative_import_depths(path: Path) -> set[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.level for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level > 0
    }


class TestDependenciesPointInward:
    @pytest.mark.parametrize("module", list(modules_under(DOMAIN)), ids=lambda p: p.name)
    def test_the_domain_imports_no_framework(self, module: Path) -> None:
        """The payoff is concrete: every business rule is tested without a Home Assistant
        instance, in milliseconds, and stays passing when HA changes an API."""
        offenders = imported_names(module) & set(FORBIDDEN_INWARD)
        assert not offenders, f"{module.name} imports {sorted(offenders)}"

    @pytest.mark.parametrize("module", list(modules_under(APPLICATION)), ids=lambda p: p.name)
    def test_the_application_imports_no_framework(self, module: Path) -> None:
        offenders = imported_names(module) & set(FORBIDDEN_INWARD)
        assert not offenders, f"{module.name} imports {sorted(offenders)}"

    def test_the_domain_does_not_reach_out_to_infrastructure(self) -> None:
        """A relative import that climbs above `domain/` is the domain reaching sideways.

        `domain/value/x.py` may use `..error` (depth 2, still inside domain). Depth 3 from
        there would be `filament_ledger/`, where infrastructure lives.
        """
        for module in modules_under(DOMAIN):
            depth_inside_domain = len(module.relative_to(DOMAIN).parts)
            max_allowed = depth_inside_domain
            for level in relative_import_depths(module):
                assert level <= max_allowed, (
                    f"{module.relative_to(INTEGRATION)} climbs {level} levels, "
                    f"escaping the domain layer"
                )


class TestImmutabilityIsUnexpressible:
    def test_the_movement_repository_exposes_no_mutation(self) -> None:
        """It looks pedantic until the afternoon someone adds one to fix a bug quickly.

        The interface makes the invariant unexpressible rather than merely discouraged.
        """
        methods = [name for name in dir(MovementRepository) if not name.startswith("_")]
        offenders = [
            name for name in methods if any(word in name.lower() for word in MUTATION_WORDS)
        ]
        assert not offenders, f"MovementRepository exposes {offenders}"

    def test_it_can_still_append(self) -> None:
        """Guards the test above from passing because the port went missing."""
        assert "append" in dir(MovementRepository)


class TestAsyncBoundary:
    """ADR-0005 puts the sync/async boundary at the I/O ports.

    The first `async def` on an entity is the first sign that I/O has leaked inward, and it
    will be added for a reason that seems good at the time.
    """

    @pytest.mark.parametrize(
        "package",
        [DOMAIN / "model", DOMAIN / "value", DOMAIN / "service"],
        ids=["model", "value", "service"],
    )
    def test_domain_entities_and_services_are_synchronous(self, package: Path) -> None:
        for module in modules_under(package):
            tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
            coroutines = [
                node.name for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
            ]
            assert not coroutines, (
                f"{module.relative_to(INTEGRATION)} defines coroutines {coroutines}; "
                f"I/O has leaked into the domain"
            )

    def test_the_clock_port_stays_synchronous(self) -> None:
        """Reading a clock is not I/O. Making it a coroutine would force every pure
        calculation that needs a timestamp to become one."""
        from custom_components.filament_ledger.domain.port.clock import Clock

        assert not inspect.iscoroutinefunction(Clock.now)

    def test_io_ports_are_asynchronous(self) -> None:
        """The other half of ADR-0005: a port that touches a disk says so in its signature."""
        from custom_components.filament_ledger.domain.port.repositories import SpoolRepository

        for name in ("get", "find_by_tag", "save"):
            assert inspect.iscoroutinefunction(getattr(SpoolRepository, name)), name


class TestLayout:
    def test_the_domain_layer_exists_where_the_specification_says(self) -> None:
        for expected in ("model", "value", "service", "port"):
            assert (DOMAIN / expected).is_dir(), f"domain/{expected}/ is missing"

    def test_every_domain_module_is_reachable(self) -> None:
        assert list(modules_under(DOMAIN)), "no domain modules found — check the layout"


class TestTheDomainIsImportableAlone:
    """Importing a submodule executes its parent packages first.

    So a module-level `import homeassistant` in the integration's `__init__.py` makes the
    entire domain unimportable without Home Assistant installed — silently, and only on a
    machine that does not have it. The CI job that installs everything *except* Home
    Assistant found exactly that on its first run; this test makes the rule local and fast.

    The rule is precise: a framework import is allowed **inside a function body**, where it
    runs only when Home Assistant is already driving us, or **inside `if TYPE_CHECKING:`**,
    where it never runs at all. Anywhere else — including nested in a `try` or a plain `if`,
    which a shallow top-level scan would miss — it executes on import and breaks the claim.
    """

    def deferred_offenders(self, module: Path) -> list[str]:
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        exempt: set[ast.AST] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                exempt.update(ast.walk(node))
            elif isinstance(node, ast.If) and _is_type_checking(node.test):
                for branch in node.body:
                    exempt.update(ast.walk(branch))

        offenders: list[str] = []
        for node in ast.walk(tree):
            if node in exempt or not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            names = (
                [alias.name.split(".")[0] for alias in node.names]
                if isinstance(node, ast.Import)
                else [(node.module or "").split(".")[0]]
            )
            offenders.extend(name for name in names if name in FORBIDDEN_INWARD)
        return offenders

    def test_the_package_root_defers_its_framework_imports(self) -> None:
        root = INTEGRATION / "__init__.py"
        offenders = self.deferred_offenders(root)
        assert not offenders, (
            f"{root.name} imports {sorted(set(offenders))} where it executes on import; "
            f"move it inside async_setup_entry so the domain stays importable alone"
        )

    def test_the_guard_catches_an_import_hidden_in_a_conditional(self, tmp_path: Path) -> None:
        """Guards the guard.

        A top-level-only scan would pass this file, which is exactly the bypass worth
        closing: the import still executes the moment the module is loaded.
        """
        sneaky = tmp_path / "__init__.py"
        sneaky.write_text(
            "import os\nif os.name:\n    from homeassistant.core import HomeAssistant\n",
            encoding="utf-8",
        )
        assert self.deferred_offenders(sneaky) == ["homeassistant"]

    def test_a_type_checking_import_is_allowed(self, tmp_path: Path) -> None:
        """The other half: annotations must stay honest, and they never execute."""
        annotated = tmp_path / "__init__.py"
        annotated.write_text(
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from homeassistant.core import HomeAssistant\n",
            encoding="utf-8",
        )
        assert self.deferred_offenders(annotated) == []

    def test_the_root_still_annotates_with_the_real_types(self) -> None:
        """Stops the first test being satisfied by deleting the annotations instead."""
        root = INTEGRATION / "__init__.py"
        tree = ast.parse(root.read_text(encoding="utf-8"), filename=str(root))
        type_checking_imports = [
            (node.module or "")
            for block in ast.walk(tree)
            if isinstance(block, ast.If) and _is_type_checking(block.test)
            for node in ast.walk(block)
            if isinstance(node, ast.ImportFrom)
        ]
        assert "homeassistant.core" in type_checking_imports


def _is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
