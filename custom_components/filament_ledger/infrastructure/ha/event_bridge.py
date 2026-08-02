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
    MovementRecorded,
    SpoolDepleted,
    SpoolMounted,
    SpoolRegistered,
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
