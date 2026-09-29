"""The printer's direct feed as a consumption key (docs/02 §2.3, v2.8).

`ExternalFeed` sits beside `TrayRef` as the other place a print draws from. What these pin is
the part that has to hold across every reader at once: the two kinds sort as one sequence,
a key resolves to the location a spool is mounted at by one rule, and a movement note names
the position in the same words the history has always used.
"""

from __future__ import annotations

import pytest

from custom_components.filament_ledger.domain.value.identifiers import (
    FIRST_HOLDER,
    ExternalFeed,
    Feed,
    HolderIndex,
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

SECOND_HOLDER = HolderIndex(2)


class TestTheSecondHolder:
    """A dual-nozzle printer has two direct feeds, and they are two positions.

    Read off the live X2D on 2026-09-23: its weight sensor keys the second holder's figure
    as `External Spool 2`, and its `active_tray` reports `ams_index: 254` for it against
    `255` for the first. Both are real, and both hold a reel at once.
    """

    def test_a_feed_that_names_no_holder_is_the_first_one(self) -> None:
        """The default every call site written before the second holder relies on."""
        assert ExternalFeed(A_PRINTER) == ExternalFeed(A_PRINTER, FIRST_HOLDER)

    def test_one_printers_two_holders_are_two_feeds(self) -> None:
        assert ExternalFeed(A_PRINTER) != ExternalFeed(A_PRINTER, SECOND_HOLDER)

    def test_each_holder_keys_its_own_figure(self) -> None:
        usage = {ExternalFeed(A_PRINTER): 1, ExternalFeed(A_PRINTER, SECOND_HOLDER): 2}
        assert len(usage) == 2
        assert usage[ExternalFeed(A_PRINTER)] == 1

    def test_the_first_holder_sorts_before_the_second(self) -> None:
        assert ExternalFeed(A_PRINTER) < ExternalFeed(A_PRINTER, SECOND_HOLDER)
        assert a_tray(4) < ExternalFeed(A_PRINTER, SECOND_HOLDER)
        assert ExternalFeed(A_PRINTER, SECOND_HOLDER) < a_tray(1, printer=ANOTHER_PRINTER)

    def test_both_holders_resolve_to_their_own_location_and_back(self) -> None:
        feed = ExternalFeed(A_PRINTER, SECOND_HOLDER)
        assert location_of(feed) == ExternalSpool(A_PRINTER, SECOND_HOLDER)
        assert feed_of(ExternalSpool(A_PRINTER, SECOND_HOLDER)) == feed
        assert ExternalSpool(A_PRINTER) != ExternalSpool(A_PRINTER, SECOND_HOLDER)

    def test_only_the_second_holder_earns_a_numeral(self) -> None:
        """A machine with one holder has no second position to be told apart from, so the
        sentence every log line and every history row already shows stays verbatim."""
        assert position_note(ExternalFeed(A_PRINTER)) == "External spool"
        assert position_note(ExternalFeed(A_PRINTER, SECOND_HOLDER)) == "External spool 2"
        assert str(ExternalFeed(A_PRINTER, SECOND_HOLDER)) == (
            "external spool 2 on printer 00000000TESTSER"
        )
        assert str(ExternalSpool(A_PRINTER, SECOND_HOLDER)) == (
            "External spool 2 on printer 00000000TESTSER"
        )


class TestOrdering:
    def test_the_direct_feed_sorts_after_every_tray_of_its_own_printer(self) -> None:
        """One canonical order for a mixed mapping: a printer's trays by AMS and slot, then
        its direct feed, then the next machine — whichever side of the comparison the
        direct feed lands on."""
        feeds: list[Feed] = [
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
            _ = ExternalFeed(A_PRINTER) < "tray"

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
