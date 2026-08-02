"""The entities: read surfaces over the ledger, never a way to change it.

`async_setup_entry` runs against the harness runtime with a sink in place of the entity
platform, so what these tests hold are the real sensor objects reading real summaries.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import cast

from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.filament_ledger.application.adjust_spool import (
    AdjustSpoolCommand,
    DiscardFilamentCommand,
    DiscardMode,
)
from custom_components.filament_ledger.application.query import SpoolSummary
from custom_components.filament_ledger.application.register_spool import RegisterSpoolCommand
from custom_components.filament_ledger.domain.model.spool import register
from custom_components.filament_ledger.domain.value.colour import Colour
from custom_components.filament_ledger.domain.value.confidence import Confidence
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.material import Material, MaterialKind
from custom_components.filament_ledger.domain.value.spool_state import SpoolState
from custom_components.filament_ledger.infrastructure.ha.runtime import LedgerConfigEntry
from custom_components.filament_ledger.infrastructure.ha.serialisers import spool_summary
from custom_components.filament_ledger.sensor import (
    NeedsWeighingSensor,
    SpoolCountSensor,
    SpoolSensor,
    TotalStockSensor,
    async_setup_entry,
)

from ..application.conftest import EPOCH, Ledger
from .conftest import Harness, a_spool, as_hass


@dataclass
class EntitySink:
    """Captures what `async_setup_entry` adds, in place of the entity platform."""

    entities: list[Entity] = field(default_factory=list)

    def __call__(self, new_entities: Iterable[Entity], update_before_add: bool = False) -> None:
        self.entities.extend(new_entities)


async def set_up_sensors(harness: Harness) -> EntitySink:
    sink = EntitySink()
    await async_setup_entry(
        as_hass(harness.hass),
        cast(LedgerConfigEntry, harness.entry),
        cast(AddEntitiesCallback, sink),
    )
    return sink


def only[T: Entity](sink: EntitySink, kind: type[T]) -> T:
    (entity,) = [e for e in sink.entities if isinstance(e, kind)]
    return entity


async def a_tiny_spool(ledger: Ledger, label: str, kind: MaterialKind) -> None:
    await ledger.use_cases.register_spool.execute(
        RegisterSpoolCommand(
            material=Material.of(kind),
            colour=Colour.parse("000000"),
            opening_weight=Grams.of("0.3"),
            core_weight=Grams.of(250),
            label=label,
        )
    )


class TestTotalStock:
    async def test_three_third_of_a_gram_spools_read_one_gram_not_zero(
        self, harness: Harness
    ) -> None:
        """The rounding regression this sensor exists to avoid: accumulate exact grams and
        round the sum once. Rounding each spool first would report 0 g for 0.9 g of stock —
        and disagree with the websocket about the same ledger."""
        for label in ("first", "second", "third"):
            await a_tiny_spool(harness.ledger, label, MaterialKind.PLA)
        await harness.coordinator.async_request_refresh()
        sink = await set_up_sensors(harness)

        total = only(sink, TotalStockSensor)
        assert total.native_value == 1
        assert total.extra_state_attributes == {"per_material": {"PLA": 1}}

    async def test_per_material_rounds_each_material_sum_once(self, harness: Harness) -> None:
        """Two 0.3 g PLA spools hold 0.6 g of PLA — 1 g. One 0.3 g PETG spool holds 0.3 g —
        0 g. The total is still the rounding of 0.9 g, not the sum of the roundings."""
        await a_tiny_spool(harness.ledger, "pla-one", MaterialKind.PLA)
        await a_tiny_spool(harness.ledger, "pla-two", MaterialKind.PLA)
        await a_tiny_spool(harness.ledger, "petg", MaterialKind.PETG)
        await harness.coordinator.async_request_refresh()
        sink = await set_up_sensors(harness)

        total = only(sink, TotalStockSensor)
        assert total.native_value == 1
        assert total.extra_state_attributes == {"per_material": {"PLA": 1, "PETG": 0}}


class TestSpoolSensor:
    async def test_state_is_the_balance_and_attributes_are_the_summary(
        self, harness: Harness
    ) -> None:
        spool_id = await a_spool(harness.ledger, label="PLA Basic Black")
        await harness.ledger.use_cases.adjust_spool.execute(
            AdjustSpoolCommand(spool_id=spool_id, amount=Grams.of("-137.5"), reason="vase")
        )
        await harness.coordinator.async_request_refresh()
        sink = await set_up_sensors(harness)

        sensor = only(sink, SpoolSensor)
        (summary,) = harness.coordinator.data or []
        # 862.5 g rounds half-up to 863 — the same discipline as every other surface.
        assert sensor.native_value == 863
        assert sensor.available is True
        assert sensor.unique_id == f"filament_ledger_spool_{spool_id}"
        assert sensor.extra_state_attributes == spool_summary(summary)

    async def test_a_discarded_spools_sensor_goes_unavailable_rather_than_lying(
        self, harness: Harness
    ) -> None:
        spool_id = await a_spool(harness.ledger)
        await harness.coordinator.async_request_refresh()
        sink = await set_up_sensors(harness)

        await harness.ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(spool_id=spool_id, mode=DiscardMode.WHOLE_SPOOL, reason="gone")
        )
        await harness.coordinator.async_request_refresh()

        sensor = only(sink, SpoolSensor)
        assert sensor.available is False
        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}


class TestAggregates:
    async def test_the_count_tracks_stock_not_history(self, harness: Harness) -> None:
        """Discarded spools leave the count; depleted spools stay — the physical object is
        still on the shelf."""
        await a_spool(harness.ledger, label="full")
        emptied = await a_spool(harness.ledger, label="emptied")
        binned = await a_spool(harness.ledger, label="binned")
        await harness.ledger.use_cases.adjust_spool.execute(
            AdjustSpoolCommand(spool_id=emptied, amount=Grams.of(-1000), reason="all spent")
        )
        await harness.ledger.use_cases.discard_filament.execute(
            DiscardFilamentCommand(spool_id=binned, mode=DiscardMode.WHOLE_SPOOL, reason="gone")
        )
        await harness.coordinator.async_request_refresh()
        sink = await set_up_sensors(harness)

        assert only(sink, SpoolCountSensor).native_value == 2

    async def test_needs_weighing_names_the_spools_to_take_to_the_scale(
        self, harness: Harness
    ) -> None:
        """LOW confidence comes from estimated consumption, which only the Phase 2 printer
        gateway records — so the drifting summary is built by hand from real domain
        objects. The sensor's contract is over summaries, not over how the estimate got
        there."""
        await a_spool(harness.ledger, label="trusted")
        await harness.coordinator.async_request_refresh()
        trusted_summaries = list(harness.coordinator.data or [])

        drifting = SpoolSummary(
            spool=register(
                material=Material.of(MaterialKind.PLA),
                colour=Colour.parse("000000"),
                opening_weight=Grams.of(1000),
                core_weight=Grams.of(250),
                registered_at=EPOCH,
                label="drifting",
            ),
            balance=Grams.of(400),
            state=SpoolState.ACTIVE,
            confidence=Confidence.LOW,
            movement_count=4,
            last_movement_at=EPOCH,
            has_anomaly=False,
        )
        harness.coordinator.data = [*trusted_summaries, drifting]
        sink = await set_up_sensors(harness)

        needs = only(sink, NeedsWeighingSensor)
        assert needs.native_value == 1
        assert needs.extra_state_attributes == {
            "spools": [{"id": drifting.spool.id, "name": "drifting"}]
        }


class TestNewSpoolsGetEntitiesImmediately:
    async def test_a_spool_registered_while_running_appears_on_the_next_refresh(
        self, harness: Harness
    ) -> None:
        """Without the coordinator listener, a spool registered through the panel would
        only get its entity after a restart — the "works, but not until later" behaviour
        that makes people stop trusting an integration."""
        sink = await set_up_sensors(harness)
        assert [type(e).__name__ for e in sink.entities] == [
            "TotalStockSensor",
            "SpoolCountSensor",
            "NeedsWeighingSensor",
        ]
        # The listener is registered and bound to the entry's lifecycle.
        assert harness.coordinator.listeners
        assert harness.entry.unload_callbacks

        spool_id = await a_spool(harness.ledger)
        await harness.coordinator.async_request_refresh()

        spool_sensors = [e for e in sink.entities if isinstance(e, SpoolSensor)]
        assert [s.unique_id for s in spool_sensors] == [f"filament_ledger_spool_{spool_id}"]
        assert harness.runtime.known_spool_ids == {spool_id}

    async def test_a_second_refresh_does_not_duplicate_entities(self, harness: Harness) -> None:
        sink = await set_up_sensors(harness)
        await a_spool(harness.ledger)

        await harness.coordinator.async_request_refresh()
        await harness.coordinator.async_request_refresh()

        assert len([e for e in sink.entities if isinstance(e, SpoolSensor)]) == 1

    async def test_spools_known_before_setup_get_their_entities_at_setup(
        self, harness: Harness
    ) -> None:
        first = await a_spool(harness.ledger, label="first")
        second = await a_spool(harness.ledger, label="second")
        await harness.coordinator.async_request_refresh()

        sink = await set_up_sensors(harness)

        spool_sensors = [e for e in sink.entities if isinstance(e, SpoolSensor)]
        assert {s.unique_id for s in spool_sensors} == {
            f"filament_ledger_spool_{first}",
            f"filament_ledger_spool_{second}",
        }
