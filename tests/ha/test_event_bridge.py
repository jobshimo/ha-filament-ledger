"""Domain events crossing onto the Home Assistant bus.

The domain raises its events without knowing Home Assistant exists; the bridge is the one
place that speaks both vocabularies. These tests pin the wire contract automations are
written against: the `filament_ledger_*` names and the payload of each.
"""

from __future__ import annotations

import pytest

from custom_components.filament_ledger.domain.event import (
    AmbiguousTagDetected,
    AnomalyDetected,
    ConfidenceDegraded,
    DomainEvent,
    MovementRecorded,
    SpoolDepleted,
    SpoolDetected,
    SpoolMounted,
    SpoolRegistered,
    SpoolUnmounted,
    UnknownSpoolDetected,
)
from custom_components.filament_ledger.domain.service.anomaly_detector import (
    Anomaly,
    AnomalyKind,
)
from custom_components.filament_ledger.domain.value.confidence import Confidence
from custom_components.filament_ledger.domain.value.grams import Grams
from custom_components.filament_ledger.domain.value.identifiers import (
    SlotIndex,
    SpoolId,
    TagUid,
)
from custom_components.filament_ledger.domain.value.movement_type import MovementType
from custom_components.filament_ledger.infrastructure.ha.event_bridge import (
    HomeAssistantEventBus,
)

from .conftest import FakeHass, as_hass

SPOOL = SpoolId("spool-1")


class TestTranslation:
    @pytest.mark.parametrize(
        ("event", "name", "payload"),
        [
            pytest.param(
                SpoolRegistered(spool_id=SPOOL, display_name="PLA Basic Black"),
                "filament_ledger_spool_registered",
                {"spool_id": "spool-1", "name": "PLA Basic Black"},
                id="a-spool-is-registered",
            ),
            pytest.param(
                SpoolMounted(spool_id=SPOOL, slot=SlotIndex(2)),
                "filament_ledger_spool_mounted",
                {"spool_id": "spool-1", "slot": 2},
                id="a-spool-is-mounted",
            ),
            pytest.param(
                SpoolUnmounted(spool_id=SPOOL),
                "filament_ledger_spool_unmounted",
                {"spool_id": "spool-1"},
                id="a-spool-is-unmounted",
            ),
            pytest.param(
                MovementRecorded(
                    spool_id=SPOOL,
                    movement_type=MovementType.MANUAL_ADJUSTMENT,
                    amount=Grams.of("-84.1"),
                    new_balance=Grams.of("640.1"),
                ),
                "filament_ledger_movement_recorded",
                {
                    "spool_id": "spool-1",
                    "type": "MANUAL_ADJUSTMENT",
                    "amount_g": -84.1,
                    "new_balance_g": 640.1,
                },
                id="a-movement-lands",
            ),
            pytest.param(
                SpoolDepleted(spool_id=SPOOL, display_name="PLA Basic Black"),
                "filament_ledger_spool_depleted",
                {"spool_id": "spool-1", "name": "PLA Basic Black"},
                id="a-spool-runs-out",
            ),
            pytest.param(
                ConfidenceDegraded(
                    spool_id=SPOOL, previous=Confidence.HIGH, current=Confidence.MEDIUM
                ),
                "filament_ledger_confidence_degraded",
                {"spool_id": "spool-1", "from": "HIGH", "to": "MEDIUM"},
                id="a-balance-loses-trust",
            ),
            pytest.param(
                AnomalyDetected(
                    anomaly=Anomaly(
                        spool_id=SPOOL,
                        kind=AnomalyKind.NEGATIVE_BALANCE,
                        detail="balance is -40 g",
                    )
                ),
                "filament_ledger_anomaly_detected",
                {"spool_id": "spool-1", "kind": "NEGATIVE_BALANCE", "detail": "balance is -40 g"},
                id="something-is-implausible",
            ),
            pytest.param(
                SpoolDetected(tag_uid=TagUid("A1B2C3D4"), slot=SlotIndex(3)),
                "filament_ledger_spool_detected",
                {"tag_uid": "A1B2C3D4", "slot": 3},
                id="a-tag-is-seen-with-auto-mount-off",
            ),
            pytest.param(
                UnknownSpoolDetected(tag_uid=TagUid("A1B2C3D4"), slot=SlotIndex(1)),
                "filament_ledger_unknown_spool_detected",
                {"tag_uid": "A1B2C3D4", "slot": 1},
                id="an-unrecognised-tag-appears",
            ),
            pytest.param(
                AmbiguousTagDetected(
                    tag_uid=TagUid("A1B2C3D4"),
                    slot=SlotIndex(1),
                    candidates=(SpoolId("spool-1"), SpoolId("spool-2")),
                ),
                "filament_ledger_ambiguous_tag_detected",
                {
                    "tag_uid": "A1B2C3D4",
                    "slot": 1,
                    "candidate_spool_ids": ["spool-1", "spool-2"],
                },
                id="a-tag-matches-two-spools",
            ),
        ],
    )
    async def test_each_event_lands_under_its_documented_name(
        self, event: DomainEvent, name: str, payload: dict[str, object]
    ) -> None:
        hass = FakeHass()

        await HomeAssistantEventBus(as_hass(hass)).publish(event)

        assert hass.bus.fired == [(name, payload)]

    async def test_an_event_the_bridge_does_not_know_still_reaches_the_bus(self) -> None:
        """A new domain event must never vanish silently just because the bridge has not
        learned its shape yet; it lands under the generic name, carrying its type."""
        hass = FakeHass()

        await HomeAssistantEventBus(as_hass(hass)).publish(DomainEvent())

        assert hass.bus.fired == [("filament_ledger_event", {"type": "DomainEvent"})]
