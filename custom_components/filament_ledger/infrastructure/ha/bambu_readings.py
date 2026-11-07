"""One `ha-bambulab` state, read into the ledger's vocabulary — the printer boundary's readers.

Every function here takes a Home Assistant `State` (or one value off its attributes) and
answers with a domain value or `None`. They are **total**: a missing, unavailable or
malformed reading becomes `None`, never an exception, because `BambuLabGateway` calls them
from `@callback`s that promised the event loop they never raise, and a missing figure is not
a figure of zero (docs/03 §3.8).

Split out of `bambu_gateway.py`, which keeps the subscriptions and the job lifecycle and is
the only caller. The boundary's full contract is written down there and in docs/05 §5.8.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import State

from ...domain.error import InvalidValueError
from ...domain.value.colour import Colour
from ...domain.value.grams import Grams
from ...domain.value.identifiers import (
    ABSENT_TAG_SENTINEL,
    FIRST_HOLDER,
    MAX_EXTERNAL_HOLDER,
    AmsIndex,
    ExternalFeed,
    Feed,
    HolderIndex,
    PrinterSerial,
    ReelUid,
    SlotIndex,
    TagUid,
    TrayRef,
)
from ...domain.value.tray_reading import TrayReading

LOGGER = logging.getLogger(__name__)


# The per-tray attribute keys on the weight sensor, as upstream writes them. Strings in
# an attribute dictionary with no schema and no version (docs/05 §5.8) — which is why
# the translation is fixture-tested rather than believed.
#
# `get_print_weights` writes exactly these: `External Spool` when the first holder is
# active, `External Spool 2` when the second is, and otherwise `AMS <i//4+1> Tray <i%4+1>`
# for sixteen slots. The holder keys and the AMS keys are mutually exclusive — upstream
# branches between them — which is why a reading naming neither is silence rather than a
# claim that nothing was drawn (`tray_plan`).
_TRAY_WEIGHT_KEY = re.compile(r"AMS (\d+) Tray (\d+)")


_EXTERNAL_SPOOL_KEYS = {
    "External Spool": FIRST_HOLDER,
    "External Spool 2": HolderIndex(MAX_EXTERNAL_HOLDER),
}


def attribute_index(state: State, key: str) -> int | None:
    """One integer attribute, or `None` — never parsed out of a string.

    `bool` is refused before `int` because it is one in Python, and `True` reading as
    position 1 would be a position invented out of a flag.
    """
    value = state.attributes.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        LOGGER.debug("%s reads %r, which is not a position index", key, value)
        return None
    return value


def read_tray(tray: TrayRef, state: State | None) -> TrayReading | None:
    """Translate one tray sensor into a reading, or `None` when it cannot be trusted.

    Total by construction: every guard below covers a constructor precondition of the
    value objects, so nothing in here can raise into the caller — which is what lets
    `_on_tray_state_change` run bare inside the event loop.
    """
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    attributes = state.attributes
    empty = attributes.get("empty")
    if not isinstance(empty, bool):
        LOGGER.debug("%s reports no usable 'empty' flag (%r); reading skipped", tray, empty)
        return None
    if empty:
        # An emptied tray describes no spool. Whatever name or colour the attributes
        # still carry is a leftover of the previous occupant, not an observation.
        return TrayReading(tray=tray, tag=None, empty=True)
    return TrayReading(
        tray=tray,
        tag=_tag(attributes.get("tag_uid")),
        empty=False,
        # The field that says *which reel*, read at last. `tag_uid` names the chip the AMS
        # reached, and which chip that is follows the tray's parity — so a ledger keyed on
        # it lost a reel every time the reel changed side of the machine. `tray_uuid` is
        # what Bambu Studio shows as the reel's SN and it does not move (docs/12).
        reel=_reel(attributes.get("tray_uuid")),
        name=non_blank_text(attributes.get("name")),
        material=non_blank_text(attributes.get("type")),
        colour=_colour(attributes.get("color")),
        weight=_reel_weight(attributes.get("tray_weight")),
    )


def _tag(value: object) -> TagUid | None:
    """Sixteen zeros means nothing was read — absence, never an identity (docs/12).

    Translating the sentinel to `None` is this boundary's job; `TagUid` refusing the same
    string is the domain's backstop, not the translation.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text == ABSENT_TAG_SENTINEL:
        return None
    return TagUid(text)


def _reel(value: object) -> ReelUid | None:
    """Thirty-two zeros means the reel was not identified — absence, never an identity.

    The same translation `_tag` performs one field over, and it has to be performed here
    for the same reason: `ReelUid` refuses the sentinel, so a boundary that passed it
    through would raise inside a `@callback` that promised the event loop it never would.

    Any all-zero string is treated as the sentinel rather than only the exact
    thirty-two-character one. The width is firmware's to choose, absence is not, and a
    reading padded to a different length must not become an identity that merges every
    unidentifiable reel in the ledger into one.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or set(text) == {"0"}:
        return None
    return ReelUid(text)


def non_blank_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _colour(value: object) -> Colour | None:
    """The printer speaks `#RRGGBBAA`; a hint that fails to parse is dropped, not fatal."""
    if not isinstance(value, str):
        return None
    try:
        return Colour.parse(value)
    except InvalidValueError:
        LOGGER.debug("unparseable colour hint %r ignored", value)
        return None


def _weight(value: object) -> Grams | None:
    """A per-tray figure: a non-negative number, or nothing. Negative consumption and
    non-numeric shapes are upstream noise, not data.

    **A numeric string is a number.** Upstream writes the holder's figure twice at every
    start: once as a number, and again as the text it read off the 3MF once the FTP parse
    lands — `"External Spool": 49.59` and then `"49.59"`, one to forty seconds apart, on
    every print of the reference instance (docs/12-field-notes.md, 2026-09-07). The
    second write is the last thing the sensor says for the whole print. Refusing it made
    every such print's final reading a recognised key with no figure, and that stood in
    for the real one at the ending. `Grams.of` reads a decimal string exactly, so the
    text is admitted on the same terms as the number and rejected on the same terms as
    any other shape — `"lots"` and `""` raise where `inf` does.

    **Total, including the shapes a type check waves through.** `Grams.of` raises
    `InvalidValueError` on `NaN`, `inf`, `-inf`, figures too large to quantise and strings
    that are not decimals — the floats among them pass the guard above. That gap was
    survivable while this ran twice per job, from a coroutine; it is not now that it runs
    on every republish from `_on_weight_state_change`, which is a `@callback` promising
    the event loop it never raises. Caught the same way `_reel_weight` catches it, for
    the same reason.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        grams = Grams.of(value)
    except InvalidValueError:
        LOGGER.debug("per-tray figure %r is not a usable quantity; skipped", value)
        return None
    return None if grams.is_negative else grams


@dataclass(frozen=True, slots=True)
class WeightObservation:
    """One weight-sensor reading, whole — the unit the gateway holds and compares.

    Everything the reading said, not merely the part that is consumed: `plan` is what a
    job is charged with, and `unknown_positions` is what has to be *announced* about it.
    They travel together because they are deduped together, so the announcement fires once
    per new reading rather than once per republish.

    The external-spool figure used to be a third member, carried only to be warned about.
    Since v2.8 it is a plan entry like any tray's, keyed by `ExternalFeed`, so a reading
    whose trays stand still while the direct feed's figure moves is a new observation by
    the same comparison that catches a tray moving. Since v2.9 the second holder's figure
    is one too, and so is every AMS ordinal the printer names.
    """

    plan: dict[Feed, Grams]
    #: Keys that look like a position and resolved to none, verbatim, in reading order.
    #: This used to be `other_ams` and it used to be the ordinary case — every ordinal but
    #: the first landed in it. It is now the surprising case, which is what makes it worth
    #: announcing: a shape nobody here has seen, from a machine somebody actually owns.
    unknown_positions: tuple[str, ...] = ()


def tray_plan(printer: PrinterSerial, state: State) -> WeightObservation | None:
    """One weight-sensor reading, translated — or `None` when it said nothing.

    Total by construction, like `read_tray`: every malformed shape becomes a skipped key or a
    `None`, so the caller can run bare inside the event loop.

    `None` is **the shape that carries no usable per-tray figure** — the other half of
    each flicker pair, a sensor that never had a breakdown, and a key whose value no
    quantity can hold. All of that is silence, and the caller's whole job is to leave a
    real reading standing in its place. A key is recognised only once its figure is, so
    a shape that names a position it cannot put a number on does not translate to the
    printer naming no position: that shape replaced a held real reading with an empty
    plan, which then stood in for it at the ending (docs/12-field-notes.md,
    2026-09-07). The one shape that still speaks the dialect with an empty plan names a
    position this reader cannot resolve — a fact worth announcing once, and a different one
    from silence (docs/04-use-cases.md UC-04).

    **Every ordinal the printer names is charged, since v2.9.** Until then only `AMS 1`
    was: a figure keyed `AMS 2 Tray 1` was dropped with a warning, because the gateway
    followed one unit per machine and had no tray to land it on. It now builds the
    reference the key states, whether or not that unit was discovered — a figure that finds
    no spool opens the review line that exists for exactly this, where a dropped figure is
    grams nobody is ever told about.

    **The holder figures are plan entries too**, keyed by which holder the key names
    (`_EXTERNAL_SPOOL_KEYS`). Until v2.8 the first was carried out only to be warned about,
    because usage had no key for the holder beside the AMS; a print fed from it then ended
    with no figure and opened a review asking what the printer had already said
    (docs/12-field-notes.md, 2026-09-06). They are parsed by the same rule as a tray's
    figure and skipped on the same terms.

    Nothing here warns. The one thing worth saying out loud — a key that names a position
    and resolves to none — rides out on the observation instead, so the caller can say it
    once per new reading rather than once per republish.
    """
    weights: dict[Feed, Grams] = {}
    unknown: list[str] = []
    recognised = False
    for key, value in state.attributes.items():
        holder = _EXTERNAL_SPOOL_KEYS.get(key)
        if holder is not None:
            grams = _weight(value)
            if grams is None:
                LOGGER.debug("external-spool figure for %r reads %r; skipped", key, value)
                continue
            recognised = True
            weights[ExternalFeed(printer, holder)] = grams
            continue
        match = _TRAY_WEIGHT_KEY.fullmatch(key)
        if match is None:
            continue
        grams = _weight(value)
        if grams is None:
            LOGGER.debug("per-tray figure for %r reads %r; skipped", key, value)
            continue
        try:
            tray = TrayRef(
                printer=printer,
                ams=AmsIndex(int(match.group(1))),
                slot=SlotIndex(int(match.group(2))),
            )
        except InvalidValueError:
            # The key looks like a position and names none — an ordinal of zero, a fifth
            # tray. Kept verbatim rather than dropped, because the shape is evidence and
            # the machine reporting it belongs to somebody who can be asked about it.
            recognised = True
            unknown.append(key)
            continue
        recognised = True
        weights[tray] = grams
    if not recognised:
        return None
    return WeightObservation(plan=weights, unknown_positions=tuple(unknown))


def _reel_weight(value: object) -> Grams | None:
    """The RFID's nominal spool weight — `tray_weight`, which the tag carries in grams.

    Deliberately *not* `_weight` above, on two counts that are policy rather than
    plumbing. The dialect differs: this field arrives as a **string** (`"1000"`), while
    the consumption figures arrive as numbers. And zero means the opposite thing: a tray
    that consumed nothing is a real figure of zero, whereas `tray_weight: "0"` is the tag
    declining to say — the reference machine writes it for the untagged third-party reel
    in tray 3 (docs/12-field-notes.md). Folding the two policies into one helper would
    make one of the two call sites wrong.

    Non-positive and unparseable both become `None`, never a fabricated number: the
    domain refuses an opening weight of nothing, and the register path reads absence as
    *fall back to the configured default* rather than as a figure.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        grams = Grams.of(value.strip() if isinstance(value, str) else value)
    except InvalidValueError:
        # `Grams.of` refuses the shapes an attribute dictionary can still hold — "", "n/a",
        # "NaN". Caught here so `read_tray` stays total, as its own docstring promises.
        LOGGER.debug("unusable tray_weight %r ignored", value)
        return None
    return grams if grams.is_positive else None
