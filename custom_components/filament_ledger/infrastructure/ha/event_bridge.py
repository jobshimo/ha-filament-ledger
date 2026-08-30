"""Translates domain events onto the Home Assistant bus.

The domain raises them without knowing Home Assistant exists. This module is the only place
that knows both vocabularies, which is what keeps `domain/event.py` free of framework types.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from homeassistant.core import HomeAssistant

from ...application.query import display_job_name
from ...const import EVENT_PREFIX
from ...domain.event import (
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
from ...domain.value.identifiers import TrayRef


def event_name(suffix: str) -> str:
    return f"{EVENT_PREFIX}{suffix}"


#: Every name this bridge can publish that means the ledger changed.
#:
#: The panel's live subscription listens for exactly these (`websocket_api.handle_subscribe`),
#: so a domain event added to `_translate` without a line here would reach automations and
#: silently stop the panel updating — the ledger right and the screen wrong.
#: `tests/ha/test_event_bridge.py` compares this set against the module's own source and
#: fails when they disagree, in both directions.
#:
#: The generic `filament_ledger_event` fallback is deliberately absent: it carries a type
#: name and nothing else, so there is nothing for a view to show differently because of it.
LEDGER_EVENTS: Final = frozenset(
    event_name(suffix)
    for suffix in (
        "spool_registered",
        "spool_mounted",
        "spool_unmounted",
        "movement_recorded",
        "movement_voided",
        "movement_reinstated",
        "movement_reassigned",
        "spool_deleted",
        "spool_restored",
        "spool_depleted",
        "confidence_degraded",
        "anomaly_detected",
        "review_opened",
        "review_resolved",
        "spool_detected",
        "unknown_spool_detected",
        "ambiguous_tag_detected",
    )
)


@dataclass(frozen=True, slots=True)
class HomeAssistantEventBus:
    """Publishes domain events as `filament_ledger_*` events on the HA bus.

    These make the interesting automations trivial for the user to write, which is the whole
    argument for a custom integration over an add-on (ADR-0003).
    """

    hass: HomeAssistant

    async def publish(self, event: DomainEvent) -> None:
        name, data = _translate(event)
        self.hass.bus.async_fire(name, data)


def _translate(event: DomainEvent) -> tuple[str, dict[str, Any]]:
    match event:
        case SpoolRegistered(spool_id, display_name):
            return event_name("spool_registered"), {
                "spool_id": spool_id,
                "name": display_name,
            }
        case SpoolMounted(spool_id, tray):
            return event_name("spool_mounted"), {"spool_id": spool_id, **_tray_fields(tray)}
        case SpoolUnmounted(spool_id):
            return event_name("spool_unmounted"), {"spool_id": spool_id}
        case MovementRecorded(spool_id, movement_type, amount, new_balance):
            return event_name("movement_recorded"), {
                "spool_id": spool_id,
                "type": movement_type.value,
                "amount_g": float(amount.as_decimal),
                "new_balance_g": float(new_balance.as_decimal),
            }
        case MovementVoided(movement_id, spool_id, returned):
            # `returned_g` is null for a void without restitution: nothing came back, and
            # an automation that read a zero would be wrong in exactly the case that
            # matters (docs/14 §14.4.1).
            return event_name("movement_voided"), {
                "movement_id": movement_id,
                "spool_id": spool_id,
                "returned_g": float(returned.as_decimal) if returned is not None else None,
            }
        case MovementReinstated(movement_id, spool_id, deducted):
            return event_name("movement_reinstated"), {
                "movement_id": movement_id,
                "spool_id": spool_id,
                "deducted_g": float(deducted.as_decimal),
            }
        case MovementReassigned(movement_id, from_spool_id, to_spool_id, amount):
            return event_name("movement_reassigned"), {
                "movement_id": movement_id,
                "from_spool_id": from_spool_id,
                "to_spool_id": to_spool_id,
                "amount_g": float(amount.as_decimal),
            }
        case SpoolDeleted(spool_id, display_name):
            return event_name("spool_deleted"), {"spool_id": spool_id, "name": display_name}
        case SpoolRestored(spool_id, display_name):
            return event_name("spool_restored"), {"spool_id": spool_id, "name": display_name}
        case SpoolDepleted(spool_id, display_name):
            return event_name("spool_depleted"), {"spool_id": spool_id, "name": display_name}
        case ConfidenceDegraded(spool_id, previous, current):
            return event_name("confidence_degraded"), {
                "spool_id": spool_id,
                "from": previous.value,
                "to": current.value,
            }
        case AnomalyDetected(anomaly):
            return event_name("anomaly_detected"), {
                "spool_id": anomaly.spool_id,
                "kind": anomaly.kind.value,
                "detail": anomaly.detail,
            }
        case ReviewOpened(review_id, job_id, job_name, reason):
            # `job_display_name` is the readable form a notification can lead with —
            # the raw name stays, because it is the identity automations may match on.
            return event_name("review_opened"), {
                "review_id": review_id,
                "job_id": job_id,
                "job_name": job_name,
                "job_display_name": display_job_name(job_name),
                "reason": reason.value,
            }
        case ReviewResolved(review_id, job_id, state):
            return event_name("review_resolved"), {
                "review_id": review_id,
                "job_id": job_id,
                "state": state.value,
            }
        case SpoolDetected(tag_uid, tray):
            return event_name("spool_detected"), {
                "tag_uid": tag_uid.value,
                **_tray_fields(tray),
            }
        case UnknownSpoolDetected(tag_uid, tray):
            return event_name("unknown_spool_detected"), {
                "tag_uid": tag_uid.value,
                **_tray_fields(tray),
            }
        case AmbiguousTagDetected(tag_uid, tray, candidates):
            return event_name("ambiguous_tag_detected"), {
                "tag_uid": tag_uid.value,
                **_tray_fields(tray),
                "candidate_spool_ids": list(candidates),
            }
        case _:
            return event_name("event"), {"type": type(event).__name__}


def _tray_fields(tray: TrayRef) -> dict[str, Any]:
    """The tray an event happened in, on the bus.

    `slot` keeps its name and its meaning, so an automation matching on it goes on
    matching; `printer` and `ams` join it, because a bus payload that named a tray number
    alone would stop identifying a tray the moment a second machine existed.
    """
    return {"printer": tray.printer.value, "ams": tray.ams.value, "slot": tray.slot.value}
