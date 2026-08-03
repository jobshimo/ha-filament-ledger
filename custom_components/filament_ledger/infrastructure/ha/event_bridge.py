"""Translates domain events onto the Home Assistant bus.

The domain raises them without knowing Home Assistant exists. This module is the only place
that knows both vocabularies, which is what keeps `domain/event.py` free of framework types.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant

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


def event_name(suffix: str) -> str:
    return f"{EVENT_PREFIX}{suffix}"


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
        case SpoolMounted(spool_id, slot):
            return event_name("spool_mounted"), {"spool_id": spool_id, "slot": slot.value}
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
            return event_name("review_opened"), {
                "review_id": review_id,
                "job_id": job_id,
                "job_name": job_name,
                "reason": reason.value,
            }
        case ReviewResolved(review_id, job_id, state):
            return event_name("review_resolved"), {
                "review_id": review_id,
                "job_id": job_id,
                "state": state.value,
            }
        case SpoolDetected(tag_uid, slot):
            return event_name("spool_detected"), {
                "tag_uid": tag_uid.value,
                "slot": slot.value,
            }
        case UnknownSpoolDetected(tag_uid, slot):
            return event_name("unknown_spool_detected"), {
                "tag_uid": tag_uid.value,
                "slot": slot.value,
            }
        case AmbiguousTagDetected(tag_uid, slot, candidates):
            return event_name("ambiguous_tag_detected"), {
                "tag_uid": tag_uid.value,
                "slot": slot.value,
                "candidate_spool_ids": list(candidates),
            }
        case _:
            return event_name("event"), {"type": type(event).__name__}
