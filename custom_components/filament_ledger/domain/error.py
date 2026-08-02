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


class InvalidValueError(DomainError):
    """A value object was constructed with something that cannot exist."""


class SpoolDiscardedError(DomainError):
    """The spool has been thrown away. It accepts no movements and cannot move."""


class DuplicateTagNotConfirmedError(DomainError):
    """Registering a spool whose tag already belongs to another, without saying so.

    Duplicates are legal - a Bambu tag identifies a batch, not a unit - but they must be
    deliberate, never accidental.
    """


class AmbiguousTagError(DomainError):
    """An RFID resolved to more than one non-discarded spool.

    Never resolved by picking one. Choosing wrong means every subsequent print drains a
    spool sitting on a shelf while the one in the machine runs out unannounced.
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
