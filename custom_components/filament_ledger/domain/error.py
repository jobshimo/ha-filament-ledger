"""Domain errors.

Every one of these means "the caller asked for something the rules do not allow". None of
them means "something went wrong internally" — that is what unhandled exceptions are for.

The `Error` suffix is a Python convention rather than the language of the problem. It is
kept because this integration is meant to be readable by Home Assistant contributors, and a
codebase that argues with `pep8-naming` on every exception spends its credibility oddly.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base for every rule violation the domain refuses."""


class InvalidValueError(DomainError, ValueError):
    """A value object was constructed with something that cannot exist.

    Also a `ValueError`, deliberately. Every value object raises this rather than a bare
    `ValueError`, which means an adapter that catches `DomainError` catches **all** of them
    — a malformed colour, an out-of-range slot, a blank tag — without having to enumerate
    the fields.

    An earlier version had the value objects raising plain `ValueError`, so each adapter had
    to re-validate every field it forwarded. That is a list somebody forgets to extend, and
    a forgotten entry surfaces as a stack trace instead of a message.
    """


class SpoolDiscardedError(DomainError):
    """The spool has been thrown away. It accepts no movements and cannot move."""


class SpoolDeletedError(DomainError):
    """The spool's registration was retracted — it was never really here (docs/14 §14.4.3).

    Distinct from `SpoolDiscardedError` because the two say opposite things about the
    world: a discard is a real event that counts as waste, a deletion is a bookkeeping
    correction that counts as nothing. They also differ in what the user should do next —
    a discard is final, a deletion is undone from the Trash — and an error that cannot
    tell the two apart cannot say so.
    """


class DuplicateTagNotConfirmedError(DomainError):
    """Registering a spool whose tag already belongs to another, without saying so.

    Duplicates are legal - a Bambu tag identifies a batch, not a unit - but they must be
    deliberate, never accidental.
    """


class TagNotEditableError(DomainError):
    """The tag was attached by the printer, so it is the printer's statement, not the
    user's (docs/14 §14.2).

    A `DETECTED` tag matches the physical spool by construction — the sync pass read it
    off the tray. Letting it be retyped or cleared would let the ledger's tag drift from
    the reel in the machine, and the next automatic mount would then charge the wrong
    spool. Retyping a tag the user typed is another matter entirely, which is exactly why
    provenance is stored.
    """


class AmbiguousTagError(DomainError):
    """An RFID resolved to more than one non-discarded spool.

    Never resolved by picking one. Choosing wrong means every subsequent print drains a
    spool sitting on a shelf while the one in the machine runs out unannounced.
    """


class ReviewAlreadyPendingError(DomainError):
    """A job already has an open review; a second would split one decision across two items.

    The partial unique index `idx_review_job_pending` enforces the same rule at the last
    possible layer. This error is the use case saying it in the language of the problem
    instead of letting an IntegrityError surface — a constraint name is not an answer a
    user can act on.
    """


class ReviewAlreadyResolvedError(DomainError):
    """A review that is APPROVED or DISMISSED cannot be resolved again.

    Without this, a double-click deducts twice - and in a ledger, a duplicate entry is
    indistinguishable from a real one after the fact.
    """


class UnresolvedSlotError(DomainError):
    """A review slot carries a non-zero amount but no spool to charge it to.

    Refused rather than rounded. The alternatives are inventing a spool or dropping a real
    consumption on the floor, and the second is worse because it leaves no trace.
    """


class NothingToRecordError(DomainError):
    """The requested operation would produce a zero movement, which records nothing."""


class MovementNotVoidableError(DomainError):
    """This kind of entry is never deleted from the history the user sees (docs/14 §14.4.1).

    Two types refuse, each for its own reason, and the message says which:

    - `OPENING_BALANCE` — a spool is born with a ledger entry (UC-01), so a spool whose
      opening entry is voided has a balance with no origin. The operation that removes a
      mistaken registration is *delete the spool*, which retires the whole story coherently.
    - `VOID_REVERSAL` — a correction is corrected through its own flow, restore, never by
      voiding the correction, which would fork the provenance chain into two competing
      readings of the same grams.
    """


class MovementAlreadyVoidedError(DomainError):
    """The entry already has an open void chapter.

    A movement is voided at most once, ever: `movement_void`'s primary key enforces it and
    the use case checks first so the refusal is a sentence rather than a constraint name.
    Chains re-void the *reinstatement*, which is a different movement with a different id,
    so the key never has to bend (docs/14 §14.4.2).
    """


class MovementNotVoidedError(DomainError):
    """Nothing to restore: the entry has no void chapter, or its chapter is already closed.

    Restoring twice would deduct the grams twice, and in a ledger a duplicate entry is
    indistinguishable from a real one after the fact.
    """


class VoidNotReinstatableError(DomainError):
    """The void returned nothing, so there is nothing to deduct again (docs/14 §14.4.1).

    A without-restitution void is terminal by construction: the spool was out of inventory
    when the entry was deleted, no reversal was written, and the movement still sums into
    its balance. "Deduct it again" would double-charge. If the grams should move after all,
    UC-10's free-form adjustment is the honest tool and the void row's reason is the
    pointer to why.
    """


class RestitutionUnavailableError(DomainError):
    """The grams have nowhere to go back to (docs/14 §14.4.1).

    Filament only returns to a spool that is in inventory; a reversal landing on a
    discarded or deleted spool would be a balance change nobody can see. Refused rather
    than silently downgraded to a without-restitution void, because a silent downgrade is
    a gram count that changed meaning without the user noticing.
    """


class MovementNotReassignableError(DomainError):
    """Only a charge can be moved to another spool (docs/14 §14.3).

    An entry that *added* filament has no charge to move. The rule is read off the entry's
    own sign rather than off its type alone, because the correction types are
    direction-`EITHER` and a reassignment's debit leg is itself reassignable — which is
    exactly the chain the specification calls legal and honest.
    """


class EstimationUnavailableError(DomainError):
    """No estimation strategy could produce a figure for this job.

    Half of the `ConsumptionEstimator` contract (docs/07-consumption-estimation.md §7.3):
    an estimator returns per-slot grams or raises this — never `None`, and never an
    invented zero, because a fabricated number in a ledger is worse than a missing one.
    The specification names it `EstimationUnavailable`; the `Error` suffix follows this
    module's convention (see the module docstring).
    """
