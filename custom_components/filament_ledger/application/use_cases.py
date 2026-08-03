"""Every operation the system performs, in one object.

Someone opening this file sees the whole surface of the product, named in the language of
the problem. That is the point of the layout in docs/03-architecture.md §3.5.
"""

from __future__ import annotations

from dataclasses import dataclass

from .adjust_spool import AdjustSpool, DiscardFilament
from .delete_spool import DeleteSpool, RestoreSpool
from .detect_spool import DetectSpool
from .move_spool import EditSpoolDetails, MountSpool, UnmountSpool
from .query import Queries
from .reassign_movement import ReassignMovement
from .reconcile_spool import ReconcileSpool
from .record_print_consumption import RecordPrintConsumption
from .register_spool import RegisterSpool
from .review_queue import ApproveReview, DismissReview, OpenPendingReview
from .track_print_job import TrackPrintJob
from .void_movement import RestoreMovement, VoidMovement


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
    track_print_job: TrackPrintJob
    record_print_consumption: RecordPrintConsumption
    open_pending_review: OpenPendingReview
    approve_review: ApproveReview
    dismiss_review: DismissReview
    # The corrections of docs/14 — v1.0. Every one of them adds history; none subtracts
    # any (docs/adr/0007), which is why they sit beside the operations they correct
    # rather than above them.
    reassign_movement: ReassignMovement
    void_movement: VoidMovement
    restore_movement: RestoreMovement
    delete_spool: DeleteSpool
    restore_spool: RestoreSpool
    queries: Queries
