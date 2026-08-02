"""Every operation the system performs, in one object.

Someone opening this file sees the whole surface of the product, named in the language of
the problem. That is the point of the layout in docs/03-architecture.md §3.5.
"""

from __future__ import annotations

from dataclasses import dataclass

from .adjust_spool import AdjustSpool, DiscardFilament
from .detect_spool import DetectSpool
from .move_spool import EditSpoolDetails, MountSpool, UnmountSpool
from .query import Queries
from .reconcile_spool import ReconcileSpool
from .register_spool import RegisterSpool
from .review_queue import ApproveReview, DismissReview, OpenPendingReview


@dataclass(frozen=True, slots=True)
class UseCases:
    register_spool: RegisterSpool
    reconcile_spool: ReconcileSpool
    discard_filament: DiscardFilament
    adjust_spool: AdjustSpool
    mount_spool: MountSpool
    unmount_spool: UnmountSpool
    detect_spool: DetectSpool
    edit_spool_details: EditSpoolDetails
    open_pending_review: OpenPendingReview
    approve_review: ApproveReview
    dismiss_review: DismissReview
    queries: Queries
