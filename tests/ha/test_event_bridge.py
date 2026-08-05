"""Domain events crossing onto the Home Assistant bus.

The domain raises its events without knowing Home Assistant exists; the bridge is the one
place that speaks both vocabularies. These tests pin the wire contract automations are
written against: the `filament_ledger_*` names and the payload of each.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from custom_components.filament_ledger.domain.event import (
    AmbiguousTagDetected,
    AnomalyDetected,
    ConfidenceDegraded,
    DomainEvent,
    MovementReassigned,
    MovementRecorded,
    MovementReinstated,
    MovementVoided,
    ReviewOpened,
    ReviewResolved,
    SpoolDeleted,
    SpoolDepleted,
    SpoolDetected,
    SpoolMounted,
    SpoolRegistered,
    SpoolRestored,
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
    MovementId,
    PrintJobId,
    ReviewId,
    SpoolId,
    TagUid,
)
from custom_components.filament_ledger.domain.value.movement_type import MovementType
from custom_components.filament_ledger.domain.value.review import ReviewReason, ReviewState
from custom_components.filament_ledger.infrastructure.ha.event_bridge import (
    LEDGER_EVENTS,
    HomeAssistantEventBus,
)

from ..application.conftest import a_tray
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
                SpoolMounted(spool_id=SPOOL, tray=a_tray(2)),
                "filament_ledger_spool_mounted",
                {"spool_id": "spool-1", "printer": "00000000TESTSER", "ams": 1, "slot": 2},
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
                ReviewOpened(
                    review_id=ReviewId("review-1"),
                    job_id=PrintJobId("job-1"),
                    job_name="bracket_v3.gcode.3mf",
                    reason=ReviewReason.CANCELLED,
                ),
                "filament_ledger_review_opened",
                {
                    "review_id": "review-1",
                    "job_id": "job-1",
                    "job_name": "bracket_v3.gcode.3mf",
                    "reason": "CANCELLED",
                },
                id="a-print-needs-a-decision",
            ),
            pytest.param(
                ReviewResolved(
                    review_id=ReviewId("review-1"),
                    job_id=PrintJobId("job-1"),
                    state=ReviewState.APPROVED,
                ),
                "filament_ledger_review_resolved",
                {"review_id": "review-1", "job_id": "job-1", "state": "APPROVED"},
                id="a-decision-is-made",
            ),
            pytest.param(
                SpoolDetected(tag_uid=TagUid("A1B2C3D4"), tray=a_tray(3)),
                "filament_ledger_spool_detected",
                {"tag_uid": "A1B2C3D4", "printer": "00000000TESTSER", "ams": 1, "slot": 3},
                id="a-tag-is-seen-with-auto-mount-off",
            ),
            pytest.param(
                UnknownSpoolDetected(tag_uid=TagUid("A1B2C3D4"), tray=a_tray(1)),
                "filament_ledger_unknown_spool_detected",
                {"tag_uid": "A1B2C3D4", "printer": "00000000TESTSER", "ams": 1, "slot": 1},
                id="an-unrecognised-tag-appears",
            ),
            pytest.param(
                AmbiguousTagDetected(
                    tag_uid=TagUid("A1B2C3D4"),
                    tray=a_tray(1),
                    candidates=(SpoolId("spool-1"), SpoolId("spool-2")),
                ),
                "filament_ledger_ambiguous_tag_detected",
                {
                    "tag_uid": "A1B2C3D4",
                    "printer": "00000000TESTSER",
                    "ams": 1,
                    "slot": 1,
                    "candidate_spool_ids": ["spool-1", "spool-2"],
                },
                id="a-tag-matches-two-spools",
            ),
            # The corrections of docs/14 §14.3-§14.4. Each one is a fact an automation
            # can act on, and the linkage travels with it so a listener never has to
            # re-derive which entry was corrected.
            pytest.param(
                MovementVoided(
                    movement_id=MovementId("mv-1"), spool_id=SPOOL, returned=Grams.of("84.1")
                ),
                "filament_ledger_movement_voided",
                {"movement_id": "mv-1", "spool_id": "spool-1", "returned_g": 84.1},
                id="an-entry-is-deleted-and-the-grams-come-back",
            ),
            pytest.param(
                MovementVoided(movement_id=MovementId("mv-1"), spool_id=SPOOL, returned=None),
                "filament_ledger_movement_voided",
                # Null, never zero: nothing came back, and a listener reading a zero
                # would be wrong in exactly the case that matters.
                {"movement_id": "mv-1", "spool_id": "spool-1", "returned_g": None},
                id="an-entry-is-deleted-without-restitution",
            ),
            pytest.param(
                MovementReinstated(
                    movement_id=MovementId("mv-1"), spool_id=SPOOL, deducted=Grams.of("-84.1")
                ),
                "filament_ledger_movement_reinstated",
                {"movement_id": "mv-1", "spool_id": "spool-1", "deducted_g": -84.1},
                id="an-entry-is-restored",
            ),
            pytest.param(
                MovementReassigned(
                    movement_id=MovementId("mv-1"),
                    from_spool_id=SPOOL,
                    to_spool_id=SpoolId("spool-2"),
                    amount=Grams.of("84.1"),
                ),
                "filament_ledger_movement_reassigned",
                {
                    "movement_id": "mv-1",
                    "from_spool_id": "spool-1",
                    "to_spool_id": "spool-2",
                    "amount_g": 84.1,
                },
                id="a-charge-moves-to-the-spool-that-fed-the-print",
            ),
            pytest.param(
                SpoolDeleted(spool_id=SPOOL, display_name="PLA Basic Black"),
                "filament_ledger_spool_deleted",
                {"spool_id": "spool-1", "name": "PLA Basic Black"},
                id="a-registration-is-retracted",
            ),
            pytest.param(
                SpoolRestored(spool_id=SPOOL, display_name="PLA Basic Black"),
                "filament_ledger_spool_restored",
                {"spool_id": "spool-1", "name": "PLA Basic Black"},
                id="a-spool-comes-back-to-inventory",
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


class TestTheLiveSubscriptionCoversAllOfThem:
    """`LEDGER_EVENTS` is what the panel's live subscription listens for.

    A domain event added to `_translate` without a line in that set would fire correctly and
    reach automations correctly, and would silently stop the panel updating — the ledger
    right and the screen wrong, which is the failure this project exists to prevent.

    The set is compared against this module's own source rather than against a second list
    written by hand, so the only way to break it is to add an event and mean it.
    """

    def _fired_names(self) -> set[str]:
        source = Path("custom_components/filament_ledger/infrastructure/ha/event_bridge.py")
        text = source.read_text(encoding="utf-8")
        body = text[text.index("def _translate(") :]
        names = set(re.findall(r'event_name\("([^"]+)"\)', body))
        # The fallback for an event the bridge has not learned yet carries a type name and
        # nothing else, so there is nothing for a view to show differently because of it.
        return {f"filament_ledger_{name}" for name in names - {"event"}}

    def test_every_event_the_bridge_can_fire_is_subscribed_to(self) -> None:
        missing = self._fired_names() - set(LEDGER_EVENTS)

        assert not missing, (
            f"the bridge fires {sorted(missing)} and the live subscription does not listen "
            "for it, so the panel will not update when it happens"
        )

    def test_nothing_is_subscribed_to_that_cannot_arrive(self) -> None:
        """The other direction: a name nothing fires is a bus listener registered per open
        panel that can never deliver, usually the fossil of a rename."""
        stale = set(LEDGER_EVENTS) - self._fired_names()

        assert not stale, f"the subscription listens for {sorted(stale)}, which nothing fires"

    def test_the_panel_carries_no_event_list_of_its_own(self) -> None:
        """The client used to hold a copy, and a copy is a thing that drifts. The server
        decides what a change is now; a list reappearing in the panel means that inverted
        again."""
        panel = Path("custom_components/filament_ledger/www/filament-ledger-panel.js")

        assert "filament_ledger_movement_recorded" not in panel.read_text(encoding="utf-8")
