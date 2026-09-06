"""The printer's direct feed as a consumption key (docs/02 §2.3, v2.8).

`ExternalFeed` sits beside `TrayRef` as the other place a print draws from. What these pin is
the part that has to hold across every reader at once: the two kinds sort as one sequence,
a key resolves to the location a spool is mounted at by one rule, and a movement note names
the position in the same words the history has always used.
"""

from __future__ import annotations

import pytest

from custom_components.filament_ledger.domain.value.identifiers import (
    ExternalFeed,
    PrinterSerial,
    position_note,
)
from custom_components.filament_ledger.domain.value.location import (
    AmsSlot,
    ExternalSpool,
    feed_of,
    location_of,
)

from .conftest import A_PRINTER, a_tray

# Sorts after `A_PRINTER` (`…TESTSER`), so the expected sequences below read printer by
# printer in the order the ordering promises.
ANOTHER_PRINTER = PrinterSerial("00000000ZZZZZSR")


class TestOrdering:
    def test_the_direct_feed_sorts_after_every_tray_of_its_own_printer(self) -> None:
        """One canonical order for a mixed mapping: a printer's trays by AMS and slot, then
        its direct feed, then the next machine — whichever side of the comparison the
        direct feed lands on."""
        feeds = [
            ExternalFeed(ANOTHER_PRINTER),
            a_tray(2, printer=ANOTHER_PRINTER),
            ExternalFeed(A_PRINTER),
            a_tray(4),
            a_tray(1),
        ]

        assert sorted(feeds) == [
            a_tray(1),
            a_tray(4),
            ExternalFeed(A_PRINTER),
            a_tray(2, printer=ANOTHER_PRINTER),
            ExternalFeed(ANOTHER_PRINTER),
        ]
        assert min(feeds) == a_tray(1)
        assert max(feeds) == ExternalFeed(ANOTHER_PRINTER)

    def test_comparisons_hold_in_both_directions(self) -> None:
        assert a_tray(4) < ExternalFeed(A_PRINTER)
        assert ExternalFeed(A_PRINTER) > a_tray(4)
        assert ExternalFeed(A_PRINTER) <= ExternalFeed(A_PRINTER)
        assert ExternalFeed(A_PRINTER) < ExternalFeed(ANOTHER_PRINTER)
        assert ExternalFeed(A_PRINTER) < a_tray(1, printer=ANOTHER_PRINTER)

    def test_a_stranger_is_refused_rather_than_ordered(self) -> None:
        with pytest.raises(TypeError):
            _ = ExternalFeed(A_PRINTER) < "tray"  # type: ignore[operator]

    def test_it_is_a_dictionary_key_distinct_from_every_tray(self) -> None:
        usage = {a_tray(1): 1, ExternalFeed(A_PRINTER): 2, ExternalFeed(A_PRINTER): 3}
        assert usage == {a_tray(1): 1, ExternalFeed(A_PRINTER): 3}


class TestResolution:
    def test_a_feed_resolves_to_the_place_a_spool_is_mounted(self) -> None:
        assert location_of(a_tray(3)) == AmsSlot(a_tray(3))
        assert location_of(ExternalFeed(A_PRINTER)) == ExternalSpool(A_PRINTER)

    def test_a_mounted_location_resolves_back_to_its_feed(self) -> None:
        assert feed_of(AmsSlot(a_tray(3))) == a_tray(3)
        assert feed_of(ExternalSpool(A_PRINTER)) == ExternalFeed(A_PRINTER)


class TestWording:
    def test_the_note_names_the_position_without_the_machine(self) -> None:
        """The single-machine sentence the history has always shown."""
        assert position_note(a_tray(3)) == "Slot 3"
        assert position_note(ExternalFeed(A_PRINTER)) == "External spool"

    def test_str_names_the_machine_for_logs_and_errors(self) -> None:
        assert str(ExternalFeed(A_PRINTER)) == "external spool on printer 00000000TESTSER"
