"""The approval queue's item. This entity is the mechanism behind principle #1.

A review in `PENDING` has produced no movements — the balance is untouched until a person
decides. Both estimate and attribution are **frozen when the review opens**: a review may
sit in the queue for days while spools are swapped in and out of the machine, and resolving
at approval time would deduct a cancelled Tuesday print from whatever happens to be in slot
2 on Friday (docs/04-use-cases.md UC-05).

Amounts are keyed by *tray*, not by spool. The case that most needs a review is a tray that
reported usage with no spool mounted in it — there is no `SpoolId` to key that entry with.
A line says the only honest thing available: *"slot 3 used 12 g and I do not know which
spool was in it"* — and lets the user supply the missing half (docs/02-domain-model.md §2.3).
The tray is named in full — printer, AMS unit, tray — because a review may sit in the queue
for days, and a bare tray number would come back ambiguous the moment a second machine
existed to have one.

**The estimate and the attribution are not the same shape.** A tray's estimate is one
figure because the printer reports one figure per tray and can report nothing else. Its
attribution is a list, because a spool that empties mid-print and is replaced in the same
tray leaves that one figure belonging to two spools. The two were one-to-one for as long as
a tray fed from one spool for a whole print, and a model that keyed the attribution by tray
could not express the moment they stopped being.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from ..error import InvalidValueError, ReviewAlreadyResolvedError, UnresolvedSlotError
from ..value.grams import Grams, total
from ..value.identifiers import PrintJobId, ReviewId, SpoolId, TrayRef, new_review_id
from ..value.review import EstimatorKind, ReviewReason, ReviewState


@dataclass(frozen=True, slots=True)
class ReviewCharge:
    """One spool's share of a tray's consumption: which spool, and how many grams.

    Zero is permitted. A tray whose spool is known and whose figure never arrived is the
    no-data card's row (docs/06-ui-spec.md §6.3), and refusing a zero charge would make
    that row unrepresentable — the spool is a fact, the amount is what the user supplies.
    """

    spool_id: SpoolId
    amount: Grams

    def __post_init__(self) -> None:
        if self.amount.is_negative:
            msg = f"spool {self.spool_id} cannot be charged a negative {self.amount}"
            raise InvalidValueError(msg)


@dataclass(frozen=True, slots=True)
class ReviewLine:
    """One tray's frozen half-facts: the estimated amount, and how it is attributed.

    No charge at all is a fact worth recording, not an error — no spool was mounted when
    the review opened, and the approval flow is where the user supplies the answer.
    """

    tray: TrayRef
    estimated: Grams
    charges: tuple[ReviewCharge, ...] = ()

    def __post_init__(self) -> None:
        # Amounts on a review are consumption magnitudes; the sign is applied when a
        # movement is written. A negative magnitude has no physical reading.
        if self.estimated.is_negative:
            msg = f"{self.tray} cannot have a negative estimate, got {self.estimated}"
            raise InvalidValueError(msg)
        spools = [charge.spool_id for charge in self.charges]
        if len(set(spools)) != len(spools):
            # A tray's attribution answers *how many grams did each spool give*, which is
            # one figure per spool. The same spool twice is one answer written as two, and
            # every reader that maps the list back by spool would silently keep the last.
            msg = f"{self.tray} charges the same spool twice"
            raise InvalidValueError(msg)

    @property
    def attributed(self) -> Grams:
        """What this tray's charges add up to — the left-hand side of the sum invariant."""
        return total([charge.amount for charge in self.charges])


@dataclass(frozen=True, slots=True)
class PendingReview:
    id: ReviewId
    job_id: PrintJobId
    reason: ReviewReason
    lines: tuple[ReviewLine, ...]
    estimator_used: EstimatorKind
    state: ReviewState
    opened_at: datetime
    confirmed_usage: dict[TrayRef, Grams] | None = None
    resolved_at: datetime | None = None
    resolution_note: str | None = None

    def __post_init__(self) -> None:
        trays = [line.tray for line in self.lines]
        if len(set(trays)) != len(trays):
            msg = f"review {self.id} carries the same tray twice"
            raise InvalidValueError(msg)
        # A pending review with a resolution timestamp — or a resolved one without — is a
        # record that contradicts itself, and both halves would be believed by somebody.
        if (self.state is ReviewState.PENDING) != (self.resolved_at is None):
            msg = f"state {self.state} and resolved_at {self.resolved_at} contradict each other"
            raise InvalidValueError(msg)
        if self.state is ReviewState.PENDING and self.confirmed_usage is not None:
            msg = "a pending review has confirmed nothing yet"
            raise InvalidValueError(msg)

    # -- derived -----------------------------------------------------------------------

    @property
    def estimated_usage(self) -> dict[TrayRef, Grams]:
        return {line.tray: line.estimated for line in self.lines}

    @property
    def charges(self) -> list[tuple[TrayRef, ReviewCharge]]:
        """Every charge with the tray it belongs to, in tray order and then entry order.

        The flat shape the `slot_resolution` column stores, so the repository writes it
        without re-deriving anything — and the shape a reader asking *which spools does
        this print charge* wants, which no per-tray view can answer in one pass.
        """
        return [(line.tray, charge) for line in self.lines for charge in line.charges]

    @property
    def is_resolved(self) -> bool:
        return self.state.is_resolved

    @property
    def confirmed_charges(self) -> list[tuple[TrayRef, Grams, SpoolId]]:
        """Every non-zero confirmed charge with the spool it lands on, in tray order.

        Empty until approval. Zero amounts never appear — a zero movement records nothing
        and only adds noise, so `ApproveReview` must not even be tempted to write one. The
        approval invariant guarantees these add up, tray by tray, to what was confirmed,
        which is what makes the movements this produces account for the whole print.
        """
        if self.confirmed_usage is None:
            return []
        return [
            (tray, charge.amount, charge.spool_id)
            for tray, charge in self.charges
            if not charge.amount.is_zero
        ]

    # -- transitions -------------------------------------------------------------------

    def _guard_pending(self) -> None:
        if self.is_resolved:
            msg = (
                f"review {self.id} is already {self.state}; resolving it again would "
                f"deduct twice, and a duplicate ledger entry is indistinguishable from "
                f"a real one after the fact"
            )
            raise ReviewAlreadyResolvedError(msg)

    def approved(
        self,
        *,
        at: datetime,
        amounts: Mapping[TrayRef, Grams] | None = None,
        assignments: Mapping[TrayRef, SpoolId] | None = None,
        charges: Mapping[TrayRef, tuple[ReviewCharge, ...]] | None = None,
        note: str | None = None,
    ) -> PendingReview:
        """Steps 2–5 and 7 of UC-06: merge the user's corrections, refuse the unresolvable.

        User-supplied `amounts` override the frozen estimates. `charges` restates a tray's
        whole attribution, and `assignments` is its one-spool shorthand — the answer to the
        queue's commonest question, *which spool was in this tray*, which needs no
        arithmetic from the caller because one spool takes the tray whole. A tray may
        appear in one of the two, never in both: they are two answers to one question, and
        letting one win silently is how a user's second thought gets discarded. All three
        may only reference trays the review froze — the queue card renders exactly these
        rows, so an override for any other tray is a caller bug, not a decision.

        **The invariant:** each tray's charges add up to what that tray confirms. It is one
        rule where there used to be two, and it says both of them. A tray with a non-zero
        amount and nothing attributed fails it — the case UC-06 step 5 has always refused,
        because inventing a spool and dropping a real consumption on the floor are the only
        alternatives and the second is worse for leaving no trace. So does a tray with
        10 g attributed out of 300: the other 290 g came off *something*, and a ledger that
        accepted the shortfall would lose them silently. The user is one field away from
        both answers; the system is not.

        The returned review records the amounts and the attribution *actually used* — the
        decision, not the proposal. The proposal's provenance survives in `estimator_used`,
        and the untouched estimates survive per line.
        """
        self._guard_pending()
        frozen = self.estimated_usage
        for name, overrides in (
            ("amount", amounts),
            ("assignment", assignments),
            ("charge", charges),
        ):
            unknown = sorted(set(overrides or {}) - set(frozen))
            if unknown:
                stated = "; ".join(str(tray) for tray in unknown)
                msg = f"{name} supplied for tray(s) this review does not cover: {stated}"
                raise InvalidValueError(msg)
        contested = sorted(set(assignments or {}) & set(charges or {}))
        if contested:
            stated = "; ".join(str(tray) for tray in contested)
            msg = (
                f"tray(s) {stated} carry both an assignment and a charge list; "
                f"they are two answers to one question"
            )
            raise InvalidValueError(msg)
        for tray, amount in (amounts or {}).items():
            if amount.is_negative:
                msg = f"{tray} cannot be confirmed at a negative {amount}"
                raise InvalidValueError(msg)

        final_amounts = {**frozen, **(amounts or {})}
        final_lines = tuple(
            replace(
                line,
                charges=_attribution(line, final_amounts[line.tray], assignments, charges),
            )
            for line in self.lines
        )
        unbalanced = [line for line in final_lines if line.attributed != final_amounts[line.tray]]
        if unbalanced:
            stated = "; ".join(
                f"slot {line.tray.slot} confirms {final_amounts[line.tray]} "
                f"and charges {line.attributed}"
                for line in unbalanced
            )
            msg = (
                f"every tray's charges must add up to what that tray confirms, and these "
                f"do not: {stated}. Charge the difference to a spool — a tray may name "
                f"more than one — zero the amount, or dismiss"
            )
            raise UnresolvedSlotError(msg)

        return replace(
            self,
            state=ReviewState.APPROVED,
            resolved_at=at,
            resolution_note=note,
            confirmed_usage=final_amounts,
            lines=final_lines,
        )

    def dismissed(self, *, at: datetime, note: str | None = None) -> PendingReview:
        """UC-07: resolve without recording consumption.

        A recorded decision with a timestamp and a reason — not a deletion. Nothing here
        touches amounts or attribution, because nothing was confirmed.
        """
        self._guard_pending()
        return replace(self, state=ReviewState.DISMISSED, resolved_at=at, resolution_note=note)


def _attribution(
    line: ReviewLine,
    amount: Grams,
    assignments: Mapping[TrayRef, SpoolId] | None,
    charges: Mapping[TrayRef, tuple[ReviewCharge, ...]] | None,
) -> tuple[ReviewCharge, ...]:
    """One tray's charges as the decision leaves them: restated, assigned, or inherited.

    A supplied charge list is the whole answer for that tray and replaces what was frozen.
    An assignment names one spool and gives it the tray whole. Otherwise the frozen charges
    stand — except that a tray carrying exactly one of them lets that charge follow the
    confirmed amount, because with one charge the sum invariant admits exactly one split
    and nothing is being decided. Two or more frozen charges and a changed amount is a
    different matter: rescaling them would be the system choosing a split, so the invariant
    refuses and the caller restates it.
    """
    supplied = (charges or {}).get(line.tray)
    if supplied is not None:
        return supplied
    assigned = (assignments or {}).get(line.tray)
    if assigned is not None:
        return (ReviewCharge(spool_id=assigned, amount=amount),)
    if len(line.charges) == 1:
        return (replace(line.charges[0], amount=amount),)
    return line.charges


def open_review(
    *,
    job_id: PrintJobId,
    reason: ReviewReason,
    lines: tuple[ReviewLine, ...],
    estimator_used: EstimatorKind,
    opened_at: datetime,
) -> PendingReview:
    """Build a new review in `PENDING`, generating its identity.

    Lines are stored sorted by tray so that every reader — the queue card, the approval
    loop, the persisted JSON — sees the same order without each imposing its own.
    """
    return PendingReview(
        id=new_review_id(),
        job_id=job_id,
        reason=reason,
        lines=tuple(sorted(lines, key=lambda line: line.tray)),
        estimator_used=estimator_used,
        state=ReviewState.PENDING,
        opened_at=opened_at,
    )
