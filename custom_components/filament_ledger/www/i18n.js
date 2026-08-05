/**
 * Filament Ledger — the panel's string table (docs/14 §14.6.1).
 *
 * A plain ES module served from the same static directory the panel is, so a relative
 * import needs no new registration (`infrastructure/ha/panel.py` registers the whole
 * `www/` path). No framework, no build step: ADR-0006 governs this file exactly as it
 * governs the panel.
 *
 * ## The rules, stated once
 *
 * **Every user-facing string in the panel lives here.** The acceptance criterion is a
 * panel source with zero user-facing literals outside this file, and it is a greppable
 * review rule precisely because there is no JavaScript harness to assert it.
 *
 * **EN is complete and is the fallback.** A key missing from the active language falls
 * back to its English string — never to a blank and never to a bare key — because the
 * panel's strings carry the teaching voice and a hole in it reads as breakage. A key
 * missing from *both* tables is a typo in the panel, not a translation gap, so it renders
 * as the key itself: the loudest possible feedback, found in one second.
 *
 * **Placeholders are `[[name]]`, and every substituted value is escaped here.**
 *
 * The delimiter is deliberately not `{name}`. Braces in this file would be legal —
 * hassfest reads `translations/*.json`, which it parses, and never this module, which it
 * does not (docs/14 §14.6.1 states the boundary). But a string copied from here into
 * `translations/*.json` during some future tidy-up would then fail the build for a reason
 * nobody would connect to the copy, and `[[name]]` cannot collide with prose, with a
 * percent sign, or with anything hassfest looks for. The convention costs nothing and
 * removes a whole class of afternoon.
 *
 * Escaping happens at the substitution point, inside `substitute` below, and there is no
 * way to opt out. That is stronger than asking every call site to remember `esc()`:
 * a translated template is a code constant, its parameters are wire data, and the two are
 * treated accordingly. **Therefore a `t(...)` result is inserted into markup directly and
 * is never wrapped in `esc()`** — several templates carry `<b>` deliberately, and escaping
 * the template would render the tags as text. Everything that is *not* a `t(...)` result
 * still goes through `esc()` at its call site, exactly as before.
 *
 * ## Selection
 *
 * A manual override in `localStorage` wins; otherwise `hass.locale.language` with a prefix
 * match (`es-419` → `es`); otherwise English. The override lives client-side because the
 * language of *this panel on this device* is a device preference, not ledger state.
 */

export const DEFAULT_LANGUAGE = "en";
export const SUPPORTED_LANGUAGES = ["en", "es"];
export const LANGUAGE_STORAGE_KEY = "filament_ledger.language";

/**
 * HTML-escape one value. Lives here rather than in the panel because `substitute` below
 * is the busiest caller and a second copy is a second thing to keep correct.
 */
export const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );

const EN = {
  // -- chrome ------------------------------------------------------------------------
  "app.title": "Filament Ledger",
  "app.loading": "Loading…",
  "app.adminBadge": "admin",

  // -- confidence (docs/02 §2.6) -----------------------------------------------------
  "conf.HIGH": "High",
  "conf.MEDIUM": "Medium",
  "conf.LOW": "Low",
  "conf.suffix": "[[level]] confidence",

  // -- estimator provenance (docs/06 §6.3) -------------------------------------------
  "est.LINEAR_PROGRESS": "Estimated from progress · approximate",
  "est.NONE": "Reported by the printer · not an estimate",

  // -- tabs --------------------------------------------------------------------------
  "tab.inventory": "Inventory",
  "tab.history": "History",
  "tab.stats": "Stats",
  "tab.review": "Review",
  "tab.ams": "AMS",
  "tab.printer": "Printer",
  "tab.trash": "Trash",
  "tab.settings": "Settings",

  // -- movement labels in the global history (docs/06 §6.6) --------------------------
  "hist.PRINT_CONSUMPTION": "Print",
  "hist.ESTIMATED_CONSUMPTION": "Estimate (confirmed)",
  "hist.MANUAL_ADJUSTMENT": "Adjustment",
  "hist.RECONCILIATION": "Reconciliation",
  "hist.OPENING_BALANCE": "Opening balance",
  "hist.DISCARD": "Discard",
  "hist.PURGE_WASTE": "Purge",
  "hist.REASSIGNMENT": "Reassigned",
  "hist.VOID_REVERSAL": "Deleted (returned)",
  "hist.REINSTATEMENT": "Restored",

  // -- movement labels in one spool's own history -----------------------------------
  // Deliberately longer than the `hist.*` set above, and the difference is the point
  // (docs/06 §6.6): the global table shows every spool together so each word has to earn
  // its column width, while a detail row has to explain itself to somebody reading it six
  // months later with no memory of the modal. These mirror `_MOVEMENT_LABELS`
  // (`application/query.py`), which is what the wire's `label` field carries in English;
  // the panel re-derives from `type` so the column speaks the reader's language.
  "mv.PRINT_CONSUMPTION": "Print",
  "mv.ESTIMATED_CONSUMPTION": "Estimated consumption",
  "mv.MANUAL_ADJUSTMENT": "Adjustment",
  "mv.RECONCILIATION": "Reconciliation",
  "mv.OPENING_BALANCE": "Opening balance",
  "mv.DISCARD": "Discard",
  "mv.PURGE_WASTE": "Purge waste",
  "mv.REASSIGNMENT": "Reassignment",
  "mv.VOID_REVERSAL": "Deleted entry — returned",
  "mv.REINSTATEMENT": "Restored entry",
  "src.USER_CONFIRMED": "confirmed by you",
  "src.AUTOMATIC": "automatic",

  // -- where a spool is, and what state it is in -------------------------------------
  // The wire carries `location.label` pre-rendered in English *and* the `kind`/`slot`
  // pair it was built from (`describe_location`, `application/query.py`). The panel
  // rebuilds from the pair rather than printing the label, for the same reason it
  // rebuilds movement labels from `type`: a read model is data, and the sentence around
  // it has to be in the reader's language.
  "loc.AMS_SLOT": "AMS slot [[slot]]",
  "loc.EXTERNAL_SPOOL": "External spool",
  "loc.STORAGE": "Storage",
  "state.SEALED": "sealed",
  "state.ACTIVE": "active",
  "state.DEPLETED": "depleted",
  "state.DISCARDED": "discarded",
  "state.DELETED": "deleted",

  // -- relative time -----------------------------------------------------------------
  "time.today": "Today [[time]]",
  "time.yesterday": "Yesterday",
  "time.daysAgo": "[[days]] days ago",

  // -- shared actions and field labels -----------------------------------------------
  "act.cancel": "Cancel",
  "act.dismiss": "Dismiss",
  "act.open": "Open",
  "act.restore": "Restore",
  "act.record": "Record",
  "act.save": "Save",
  "act.mount": "Mount",
  "act.unmount": "Unmount",
  "act.discard": "Discard",
  "act.note": "Note",
  "act.reason": "Reason",
  "act.optional": "optional",
  "act.why": "why",
  "act.and": " and ",
  "act.spoolActions": "Spool actions",

  // -- inventory ---------------------------------------------------------------------
  "inv.emptyTitle": "No spools yet.",
  "inv.emptyBody":
    "Register the filament you own, and the ledger starts tracking every gram that leaves it.",
  "inv.emptyCta": "Register your first spool",
  "inv.emptyLoaded": "Printer already loaded?",
  "inv.emptyLoadedTail": "reads the trays and offers to register what it finds.",
  "inv.totalStock": "Total stock",
  "inv.spools": "Spools",
  "inv.needsWeighing": "Need weighing",
  "inv.newSpool": "+ New spool",
  "inv.sync": "⟳ Sync with printer",
  "inv.sealed": "Sealed",
  "inv.weighThis": "Weigh this spool",

  // -- the sync strip ----------------------------------------------------------------
  "sync.dormantTitle": "No printer connected — nothing to sync.",
  "sync.dormantBody":
    "The ledger reads trays through the Bambu Lab integration. Once it is set up, reload " +
    "Filament Ledger and this button reports real trays.",
  "sync.noTraysTitle": "The printer reported no usable trays right now.",
  "sync.noTraysBody":
    "A tray whose sensor is unavailable is omitted, never guessed empty. Try again once " +
    "the printer is reachable.",
  "sync.doneTitle": "Synced with the printer",
  "sync.slot": "Slot [[slot]]",
  "sync.empty": "empty",
  "sync.mounted": "mounted",
  "sync.detected": "detected — left in place, auto-mount is off",
  "sync.noTag": "occupied, tag unreadable — nothing automatic is possible",
  "sync.noTagHints": "occupied, tag unreadable — nothing automatic is possible ([[hints]])",
  "sync.ambiguous":
    "two spools share tag [[tag]] — mount the right one by hand, the system will not pick",
  "sync.unknownTag": "unknown tag [[tag]]",
  "sync.unknownTagHints": "unknown tag [[tag]] · [[hints]]",
  "sync.notInInventory": "not in inventory",
  "sync.register": "Register…",

  // -- AMS ---------------------------------------------------------------------------
  "ams.slot": "Slot [[slot]]",
  "ams.empty": "Empty",
  "ams.note":
    "No printer is connected yet. Slots are assigned by hand — mounting records no " +
    "movement, because moving a spool consumes no filament.",

  // -- global history ----------------------------------------------------------------
  "history.emptyTitle": "No movements yet.",
  "history.emptyBody":
    "Every gram that enters or leaves any spool lands here, newest first — prints, " +
    "corrections, discards, all in one ledger. Register a spool and its opening balance " +
    "becomes the first row.",
  "history.emptyFoot": "Nothing here can ever be edited. A correction is a new row.",
  "history.heading": "All movements",
  "history.colWhen": "When",
  "history.colSpool": "Spool",
  "history.colEntry": "Entry",
  "history.colAmount": "Amount",
  "history.colSource": "Source",
  "history.colCorrect": "Correct",
  "history.colBalance": "Balance",
  "history.foot":
    "The newest [[count]] entries, every spool together. For the running balance behind " +
    "any row, open its spool — a balance only derives within one spool's history.",
  "history.confirmed": "confirmed",
  "history.auto": "auto",
  "history.deleted": "deleted",
  "history.reassignTitle": "Move this charge to another spool",
  "history.voidTitle": "Delete this entry and return the grams",

  // -- the History filter row (docs/06 §6.6) -----------------------------------------
  // Each label says what its control actually narrows, because a control that promises
  // more than it does reads as broken rather than as narrow. The search box is the one
  // that has to be careful: it matches stored text, so it cannot claim the entry labels
  // this panel generates or the spool name the swatches beside it already cover.
  "history.filterSearch": "Note or print",
  "history.filterSearchPlaceholder": "Search…",
  "history.filterSearchHelp":
    "Matches the note written on an entry and the name of the print it came from. Not the " +
    "spool's name — the colours narrow that — and not the entry's own label.",
  "history.filterFrom": "From",
  "history.filterTo": "To",
  "history.filterAmount": "Grams moved",
  "history.filterAmountHelp":
    "How much filament moved, whichever way it went: a print that used 84 g matches at " +
    "least 50 g, and so does a correction that gave 84 g back.",
  "history.filterAtLeast": "at least",
  "history.filterAtMost": "at most",
  "history.filterColour": "Colour",
  "history.filterColourHelp":
    "Entries on spools of the colours you pick. Pick several — the blacks and the greys " +
    "are one question, not two.",
  "history.filterColourOne": "Filament [[colour]]",
  "history.filterClear": "Clear filters",
  "history.filterToggle": "Filters",
  "history.filterActive": "Narrowed by [[count]] of these controls",
  "history.noMatchTitle": "Nothing matches these filters.",
  "history.noMatchBody":
    "The ledger still holds every entry it held a moment ago; this slice of it is empty. " +
    "Widen a date, drop a colour, or clear the filters to see all of it again.",
  "history.footFiltered":
    "[[count]] entries match these filters, newest first. The limit applies to what " +
    "matched, so widening a filter can bring older entries back into view.",

  // -- statistics (docs/06 §6.7, docs/15 §15.6) --------------------------------------
  // Every figure on this tab is computed in the application layer and arrives finished:
  // the panel draws, it never aggregates. These strings are the labels on those figures
  // and the sentences that say what they do and do not count.
  "stats.periodLabel": "Period",
  "stats.period30d": "30 days",
  "stats.period90d": "90 days",
  "stats.periodall": "All time",
  "stats.consumed": "Printed",
  "stats.wasted": "Wasted",
  "stats.printsFinished": "Prints finished",
  "stats.reviewsResolved": "Reviews resolved",
  "stats.printTime": "Print time",
  "stats.printTimeAverage": "Average print",
  "stats.printTimeAcross":
    "Measured across [[count]] prints that recorded both a start and an end — not " +
    "estimated, and not every job in the period.",
  "stats.duration": "[[hours]] h [[minutes]] min",
  "stats.durationMinutes": "[[minutes]] min",
  "stats.byColour": "Filament by colour",
  "stats.byMaterial": "Filament by material",
  "stats.outcomes": "How prints ended",
  "stats.outcomeFinished": "finished",
  "stats.outcomeCancelled": "cancelled",
  "stats.outcomeFailed": "failed",
  "stats.reviewsApproved": "approved",
  "stats.reviewsDismissed": "dismissed",
  "stats.reviewsHeading": "Decisions you made",
  "stats.topPrints": "Biggest prints",
  "stats.colPrint": "Print",
  "stats.colWhen": "Started",
  "stats.colFilament": "Filament",
  "stats.noConsumption": "No filament left a spool in this period.",
  "stats.noTopPrints": "No consumption in this period is linked to a print yet.",
  "stats.noOutcomes": "No print ended in this period.",
  "stats.noReviews": "No review was decided in this period.",
  "stats.emptyTitle": "Nothing to count yet.",
  "stats.emptyBody":
    "This page adds up what the ledger already holds: how much filament your prints used, " +
    "how much was thrown away, which colours and materials go fastest, and how your prints " +
    "ended. It fills itself in as you print — nothing here is typed in by hand.",
  "stats.emptyFoot":
    "Try <b>All time</b> if you have printed before but not recently. An empty page here " +
    "means the ledger has nothing in this period, never that a figure failed to load.",
  "stats.foot":
    "Counted from the ledger, never estimated. A spool you deleted counts in nothing here, " +
    "a deleted entry counts in nothing, and a discard counts as waste rather than as " +
    "printing — the same rules the rest of the panel follows.",

  // -- review ------------------------------------------------------------------------
  "review.emptyTitle": "Nothing to review.",
  "review.emptyBody":
    "Cancelled and failed prints will appear here so you can confirm how much filament " +
    "they used. Nothing is ever deducted for them until you say so.",
  "review.emptyFoot":
    "This queue fills up once the printer is connected. Until then every movement in the " +
    "ledger is one you entered yourself.",
  "review.pending": "[[count]] pending",
  "review.completed": "completed",
  "review.stoppedAtLayer": "stopped at layer [[layer]] of [[total]]",
  "review.stoppedAtLayerPct": "stopped at layer [[layer]] of [[total]] ([[pct]]%)",
  "review.stoppedAtPct": "stopped at [[pct]]%",
  "review.printerError": "Printer error",
  "review.rawErrorTitle": "raw print_error [[code]]",
  "review.printerReported": "printer reported &quot;[[state]]&quot;",
  "review.noDataTitle": "⛔ No consumption data — the printer never reported it",
  "review.noDataBody": "Nothing has been deducted for this print.",
  "review.total": "total <b>[[grams]]</b> g",
  "review.weighedSpools": "⚖ I weighed the spools:",
  "review.weighedWaste": "⚖ I weighed the waste:",
  "review.distribute": "Distribute",
  "review.approve": "✓ Approve",
  "review.unknownSpool": "Unknown spool",
  "review.whichSpool": "which spool was in this tray?",
  "review.chooseSpool": "Choose spool…",
  "review.slotWord": "slot [[slot]]",
  "review.blockedHint": "Approve is disabled until [[slots]] has a spool, or its amount is 0.",
  "review.invalidAmounts": "Amounts must be zero or positive numbers.",

  // -- review: a tray that fed from more than one spool (docs/06 §6.3) ----------------
  "review.addSpool": "+ Add spool",
  "review.loadRest": "Load the rest",
  "review.loadRestTitle": "Charge this spool everything the tray has left",
  "review.dropChargeTitle": "Take this spool off the tray",
  "review.remaining": "[[grams]] g left to charge",
  "review.overCharged": "[[grams]] g more than this tray used",
  "review.remainderHint":
    "Approve is disabled until every gram in [[slots]] is charged to a spool.",

  // -- spool detail ------------------------------------------------------------------
  "detail.back": "← All spools",
  "detail.ofOpening": "g of [[opening]] g",
  "detail.deletedNote":
    "This spool is in the trash — treated as never registered, counted in nothing. Its " +
    "history is below, whole and unchanged, and restoring brings both back.",
  "detail.restoreSpool": "Restore this spool",
  "detail.weigh": "Weigh",
  "detail.adjust": "Adjust",
  "detail.edit": "Edit",
  "detail.finish": "Mark as finished",
  "detail.finishHelp":
    "The reel came off the printer empty. The balance is set to 0 g and the difference is " +
    "recorded as a reconciliation, so the drift is visible rather than lost.",
  "detail.remove": "Remove…",
  "detail.removeHelp":
    "Thrown away, or registered by mistake? The next step asks which, because the two are " +
    "different facts about the world.",
  "detail.heading": "Movement history",
  "detail.foot":
    "Read bottom-up it is a derivation, not an assertion. Nothing above can be edited — a " +
    "correction is a new row. Deleted entries stay here, struck through, with the row that " +
    "returned their grams beside them: this is the one view that hides nothing, because it " +
    "is the view that proves the total.",

  // -- trash -------------------------------------------------------------------------
  "trash.emptyTitle": "The trash is empty.",
  "trash.emptyBody":
    "Deleted spools and deleted history entries wait here, and everything can be restored. " +
    "Nothing in the ledger is ever truly gone — a deletion is one more entry, not one less.",
  "trash.spoolsHeading": "Spools",
  "trash.spoolsBody":
    "Registered by mistake, so counted in nothing. Restoring returns each one to storage — " +
    "its old slot was freed and is not reclaimed — and its history comes back with it.",
  "trash.spoolMeta": "[[material]] · [[balance]] g · [[count]] entries · deleted [[when]]",
  "trash.movementsHeading": "History entries",
  "trash.movementsBody":
    "Deleted from the views you read every day, never from the ledger. The entry and the " +
    "row that returned its grams are both still there, in the spool's own history.",
  "trash.movementMeta": "[[label]] · <b>[[grams]] g</b> [[direction]] · deleted [[when]]",
  "trash.movementMetaReason":
    "[[label]] · <b>[[grams]] g</b> [[direction]] · deleted [[when]] · [[reason]]",
  "trash.returned": "returned",
  "trash.removed": "removed",
  "trash.noRestitution": "nothing was returned when this was deleted; the ledger still counts it",
  "trash.spoolDeleted": "restore this entry's spool first",
  "trash.spoolDiscarded": "this entry's spool was discarded — there is nothing to deduct from",

  // -- dialog: register --------------------------------------------------------------
  "dlg.registerTitle": "Register a spool",
  "dlg.material": "Material",
  "dlg.materialOther": "Name if OTHER",
  "dlg.materialOtherPlaceholder": "Nylon-X",
  "dlg.colour": "Colour",
  "dlg.openingWeight": "Opening weight (g)",
  "dlg.coreWeight": "Empty reel weight (g)",
  "dlg.coreWeightHelp": "A scale weighs the whole spool. The ledger subtracts this for you.",
  "dlg.vendor": "Vendor",
  "dlg.vendorPlaceholder": "Bambu Lab",
  "dlg.label": "Label",
  "dlg.labelPlaceholder": "Shelf B",
  "dlg.tagFromSlot":
    "Tag [[tag]] from slot [[slot]] will be attached, so the next sync mounts this spool " +
    "by itself.",
  "dlg.register": "Register",

  // -- dialog: edit ------------------------------------------------------------------
  "dlg.editTitle": "Edit spool",
  "dlg.editClearNote":
    "An emptied Vendor or Label keeps its current value — the tag below is the only field " +
    "this dialog can clear.",
  "dlg.tag": "Tag",
  "dlg.tagDetected":
    "Attached by the printer — edit is disabled so the tag always matches the physical spool.",
  "dlg.tagPlaceholder": "none",
  "dlg.tagClear": "Clear",
  "dlg.tagYours": "Yours to change — clearing the field removes the tag.",
  "dlg.tagNone": "A spool can be given a tag here; a tag typed here stays yours to change.",
  "dlg.tagDuplicate": "This tag belongs to another spool on purpose",
  "dlg.correctHeading": "Correct the weight",
  "dlg.correctBody":
    "This writes a movement to history — the edit itself never touches the balance.",
  "dlg.setRemaining": "Set remaining filament to (g)",
  "dlg.setRemainingHelp": "Net filament, without the reel. Recorded as a reconciliation.",
  "dlg.addRemove": "Add / remove (g)",
  "dlg.addRemoveHelp": "Negative removes, positive adds. Recorded as an adjustment.",
  "dlg.adjustReason": "Reason for the adjustment",
  "dlg.adjustReasonHelp":
    "Required for an adjustment. An unexplained one is indistinguishable from a bug.",
  "dlg.correctNothing": "Leave both empty and nothing is written to history.",
  "dlg.correctReconcile":
    "Records a reconciliation of [[delta]] g — from [[from]] g to [[to]] g.",
  "dlg.correctAdjust": "Records an adjustment of [[delta]] g — the balance becomes [[after]] g.",
  // Written into the ledger as the movement's note, not rendered as chrome — so the row
  // keeps whatever language the panel that wrote it spoke, exactly like a hand-typed
  // reason. Translating it is what makes the note readable to the person who wrote it.
  "dlg.editCorrectionNote": "Corrected from the edit dialog",

  // -- dialog: weigh -----------------------------------------------------------------
  "dlg.weighTitle": "Weigh spool",
  "dlg.weighBody": "Put the whole spool on a kitchen scale.",
  "dlg.measured": "Measured weight (g)",
  "dlg.includesCore": "Includes the reel ([[core]] g)",
  "dlg.weighFoot": "This is recorded as a correction. Nothing in your history changes.",

  // -- dialog: mark a spool finished -------------------------------------------------
  // A reconciliation to zero, and the wording says so rather than inventing a vocabulary
  // for it: the user is asserting a measurement, and the delta that falls out is the
  // system's own error signal (docs/06 §6.5, UC-08).
  "dlg.finishTitle": "Mark [[name]] as finished?",
  "dlg.finishSays":
    "The ledger still says <b>[[grams]] g</b> remain. Recording an empty reel writes a " +
    "reconciliation of <b>[[delta]] g</b> — the drift every estimate has accumulated since " +
    "this spool was last weighed.",
  "dlg.finishFoot":
    "Nothing is counted as waste and nothing is charged to a print: you are stating a " +
    "measurement, and the difference is the system's own error, recorded where it can be " +
    "read. The spool stays in your inventory at 0 g until you remove it.",
  "dlg.finishConfirm": "Record an empty spool",
  // Written into the ledger as the movement's note, so it keeps the language of the panel
  // that wrote it — the same rule the edit dialog's correction note follows.
  "dlg.finishNote": "Marked as finished — the reel came off empty",

  // -- dialog: the spool action rail, collapsed --------------------------------------
  // The heading is the spool's own name, which is data rather than a string, so it is
  // escaped at the call site instead of living here. This is the line under it.
  "dlg.actionsBalance": "[[grams]] g remaining · [[state]]",

  // -- dialog: adjust ----------------------------------------------------------------
  "dlg.adjustTitle": "Adjust",
  "dlg.amount": "Amount (g)",
  "dlg.amountHelp": "Negative removes, positive adds.",
  "dlg.adjustFoot":
    "The reason is required. An unexplained adjustment is indistinguishable from a bug.",

  // -- dialog: discard ---------------------------------------------------------------
  "dlg.discardTitle": "Discard",
  "dlg.discardWhat": "What",
  "dlg.discardPartial": "Part of this spool",
  "dlg.discardWhole": "The whole spool",
  "dlg.discardAmount": "Amount (g), if partial",
  "dlg.discardReasonPlaceholder": "tangled section",

  // -- dialog: mount -----------------------------------------------------------------
  "dlg.mountTitle": "Mount in slot [[slot]]",
  "dlg.mountNone": "Every spool is already mounted. Unmount one first.",
  "dlg.mountSpool": "Spool",

  // -- dialog: dismiss review --------------------------------------------------------
  "dlg.dismissTitle": "Record no consumption for this print?",
  "dlg.dismissFoot": "Dismissal is a decision written to history, not a delete.",

  // -- dialog: reassign --------------------------------------------------------------
  "dlg.reassignTitle": "Reassign this charge",
  "dlg.reassignNone": "There is no other spool in inventory to charge.",
  "dlg.reassignSays":
    "Return <b>[[grams]] g</b> to <b>[[spool]]</b>, and charge <b>[[grams]] g</b> to the " +
    "spool you choose. The original entry stays in history, marked as reassigned.",
  "dlg.reassignTo": "Charge it to",
  "dlg.reassignAmount": "How much to move",
  "dlg.reassignAmountHelp":
    "Up to [[grams]] g, the whole charge. Move less when the spool ran out part-way " +
    "through and another finished the print.",
  "dlg.reassignFoot":
    "No reason is required: the pair names both spools and links back to the entry it " +
    "corrects, so it explains itself.",
  "dlg.reassign": "Reassign",

  // -- dialog: delete an entry -------------------------------------------------------
  "dlg.voidTitle": "Delete this entry?",
  "dlg.voidReturns": "This returns <b>[[grams]] g</b> to <b>[[spool]]</b>.",
  "dlg.voidRemoves": "This removes <b>[[grams]] g</b> from <b>[[spool]]</b>.",
  "dlg.voidFoot":
    "Nothing is erased. The entry leaves the views you read every day and waits in the " +
    "trash; the ledger records the deletion and the grams coming back as two more rows.",
  "dlg.voidConfirm": "Delete entry",
  "dlg.voidDeletedSpool":
    "<b>[[spool]]</b> is in the trash, so there is nowhere for <b>[[grams]] g</b> to go " +
    "back to.",
  "dlg.voidDiscardedSpool":
    "<b>[[spool]]</b> was discarded, so there is nowhere for <b>[[grams]] g</b> to go " +
    "back to.",
  "dlg.voidRestoreFirst": "Restore the spool first",
  "dlg.voidDiscardRoute":
    "The way back for a discarded spool is to delete its whole-spool discard entry: that " +
    "returns the balance and the spool together, in one operation.",
  "dlg.voidWhyNothing": "Why nothing comes back",
  "dlg.voidWhyPlaceholder": "say what happened",
  "dlg.voidNoRestitutionFoot":
    "Required here. The entry still counts toward its spool's balance — only the views " +
    "change — and a deletion with no explanation reads as a bug six months later. This one " +
    "cannot be restored afterwards.",
  "dlg.voidNoRestitutionConfirm": "Delete without returning grams",

  // -- dialog: restore an entry ------------------------------------------------------
  "dlg.restoreTitle": "Restore this entry?",
  "dlg.restoreDeduct": "Deduct <b>[[grams]] g</b> from <b>[[spool]]</b> again?",
  "dlg.restoreAdd": "Add <b>[[grams]] g</b> to <b>[[spool]]</b> again?",
  "dlg.restoreFoot":
    "The entry returns to your history and the ledger records the restoration as one more " +
    "row. Nothing that happened is rewritten.",

  // -- dialog: remove a spool --------------------------------------------------------
  "dlg.intentTitle": "Remove [[name]]",
  "dlg.intentAsk": "What actually happened to it?",
  "dlg.intentThrewAway": "I threw it away",
  "dlg.intentThrewAwayHelp":
    "A real event: the remaining [[grams]] g counts as waste in your statistics, and the " +
    "spool keeps its history.",
  "dlg.intentMistake": "It was registered by mistake",
  "dlg.intentMistakeHelp":
    "Treats it as never registered — counted in nothing, anywhere, and restorable from the " +
    "Trash.",

  // -- dialog: the subject went away -------------------------------------------------
  "dlg.staleTitle": "That entry has moved on",
  "dlg.staleBody":
    "The ledger changed while this was open. Close this and try again — nothing was sent.",

  // -- printer tab (docs/14 §14.5) ---------------------------------------------------
  "printer.dormantTitle": "No printer connected.",
  "printer.dormantBody":
    "The ledger reads the printer through the Bambu Lab integration. Once it is set up, " +
    "reload Filament Ledger and this tab shows what the machine reports.",
  "printer.dormantFoot":
    "Nothing is guessed in the meantime: no spinner, and no four invented trays.",
  "printer.refresh": "⟳ Refresh",
  "printer.heading": "Printer",
  "printer.status": "Status",
  "printer.job": "Job",
  "printer.progress": "Progress",
  "printer.remaining": "Remaining",
  "printer.layer": "Layer",
  "printer.layerOf": "[[current]] of [[total]]",
  "printer.online": "Online",
  "printer.connection": "Connection",
  "printer.activeTray": "Active tray",
  "printer.yes": "yes",
  "printer.no": "no",
  "printer.errorHeading": "Printer error",
  "printer.noError": "No error reported.",
  "printer.hoursHeading": "Print time recorded here",
  "printer.hoursObserved":
    "Measured across [[count]] prints this ledger has recorded since [[since]]. It is not " +
    "the machine's lifetime counter — the printer reports no such figure — so the total " +
    "starts the day Filament Ledger was installed, not the day the printer was.",
  "printer.traysHeading": "Trays",
  "printer.trayLedger": "ledger: [[spool]]",
  "printer.trayLedgerEmpty": "ledger: nothing mounted",
  "printer.noTrays": "The printer reported no usable trays right now.",
  "printer.pendingSensors":
    "Online, connection mode and active tray are not read yet. Their upstream sensor keys " +
    "have to be confirmed on a real printer before this panel claims to know them — a key " +
    "nobody verified is a key that breaks in another language.",
  "printer.readOnly":
    "Read-only, and refreshed only when you ask. Opening this tab and pressing Refresh " +
    "change nothing in the ledger — the Sync button on Inventory is the one that does.",

  // -- settings tab (docs/14 §14.6.4) ------------------------------------------------
  "settings.heading": "Defaults",
  "settings.openingWeight": "Default opening weight (g)",
  "settings.openingWeightHelp": "What a fresh spool holds. Bambu spools are 1000 g.",
  "settings.coreWeight": "Default empty spool weight (g)",
  "settings.coreWeightHelp":
    "The bare reel, without filament. A kitchen scale weighs the whole spool, so the " +
    "ledger subtracts this for you.",
  "settings.anomalyThreshold": "Flag a reconciliation this far off (%)",
  "settings.anomalyThresholdHelp":
    "When the scale disagrees with the ledger by more than this, something upstream is " +
    "systematically wrong and worth a look.",
  "settings.autoMount": "Mount spools automatically on RFID detection",
  "settings.autoMountHelp":
    "When the printer reads a known tag, record that spool as mounted in that slot.",
  "settings.save": "Save settings",
  "settings.reloadWarning": "Saving reloads Filament Ledger — a second or two.",
  "settings.saved": "Saved. Filament Ledger is reloading with these values.",
  "settings.readOnly":
    "Only an administrator can change these. They decide how the ledger behaves for " +
    "everyone in the house, so they are shown here read-only rather than hidden — a " +
    "missing tab invites \"it's broken\".",
  "settings.languageHeading": "Language",
  "settings.languageAuto": "Auto (follow Home Assistant)",
  "settings.languageEn": "English",
  "settings.languageEs": "Español",
  "settings.languageHelp":
    "This device only. It changes what this panel says, not a single thing the ledger " +
    "stores — so two people can read the same ledger in two languages.",
};

const ES = {
  // -- chrome ------------------------------------------------------------------------
  "app.title": "Filament Ledger",
  "app.loading": "Cargando…",
  "app.adminBadge": "administrador",

  // -- confianza ---------------------------------------------------------------------
  "conf.HIGH": "Alta",
  "conf.MEDIUM": "Media",
  "conf.LOW": "Baja",
  "conf.suffix": "confianza [[level]]",

  // -- procedencia de la estimación --------------------------------------------------
  "est.LINEAR_PROGRESS": "Estimado a partir del progreso · aproximado",
  "est.NONE": "Informado por la impresora · no es una estimación",

  // -- pestañas ----------------------------------------------------------------------
  "tab.inventory": "Inventario",
  "tab.history": "Historial",
  "tab.stats": "Estadísticas",
  "tab.review": "Revisión",
  "tab.ams": "AMS",
  "tab.printer": "Impresora",
  "tab.trash": "Papelera",
  "tab.settings": "Ajustes",

  // -- etiquetas de movimiento -------------------------------------------------------
  "hist.PRINT_CONSUMPTION": "Impresión",
  "hist.ESTIMATED_CONSUMPTION": "Estimación (confirmada)",
  "hist.MANUAL_ADJUSTMENT": "Ajuste",
  "hist.RECONCILIATION": "Conciliación",
  "hist.OPENING_BALANCE": "Saldo inicial",
  "hist.DISCARD": "Descarte",
  "hist.PURGE_WASTE": "Purga",
  "hist.REASSIGNMENT": "Reasignado",
  "hist.VOID_REVERSAL": "Eliminado (devuelto)",
  "hist.REINSTATEMENT": "Restaurado",

  // -- etiquetas de movimiento en el historial de una bobina -------------------------
  "mv.PRINT_CONSUMPTION": "Impresión",
  "mv.ESTIMATED_CONSUMPTION": "Consumo estimado",
  "mv.MANUAL_ADJUSTMENT": "Ajuste",
  "mv.RECONCILIATION": "Conciliación",
  "mv.OPENING_BALANCE": "Saldo inicial",
  "mv.DISCARD": "Descarte",
  "mv.PURGE_WASTE": "Purga de desperdicio",
  "mv.REASSIGNMENT": "Reasignación",
  "mv.VOID_REVERSAL": "Entrada eliminada — devuelta",
  "mv.REINSTATEMENT": "Entrada restaurada",
  "src.USER_CONFIRMED": "confirmado por usted",
  "src.AUTOMATIC": "automático",

  // -- ubicación y estado de una bobina ----------------------------------------------
  "loc.AMS_SLOT": "Bandeja [[slot]] del AMS",
  "loc.EXTERNAL_SPOOL": "Bobina externa",
  "loc.STORAGE": "Almacenamiento",
  "state.SEALED": "sellada",
  "state.ACTIVE": "activa",
  "state.DEPLETED": "agotada",
  "state.DISCARDED": "descartada",
  "state.DELETED": "eliminada",

  // -- tiempo relativo ---------------------------------------------------------------
  "time.today": "Hoy [[time]]",
  "time.yesterday": "Ayer",
  "time.daysAgo": "hace [[days]] días",

  // -- acciones y etiquetas compartidas ----------------------------------------------
  "act.cancel": "Cancelar",
  "act.dismiss": "Descartar",
  "act.open": "Abrir",
  "act.restore": "Restaurar",
  "act.record": "Registrar",
  "act.save": "Guardar",
  "act.mount": "Montar",
  "act.unmount": "Desmontar",
  "act.discard": "Descartar",
  "act.note": "Nota",
  "act.reason": "Motivo",
  "act.optional": "opcional",
  "act.why": "por qué",
  "act.and": " y ",
  "act.spoolActions": "Acciones de la bobina",

  // -- inventario --------------------------------------------------------------------
  "inv.emptyTitle": "Todavía no hay bobinas.",
  "inv.emptyBody":
    "Registre el filamento que tiene y el registro empezará a seguir cada gramo que salga " +
    "de él.",
  "inv.emptyCta": "Registrar la primera bobina",
  "inv.emptyLoaded": "¿La impresora ya está cargada?",
  "inv.emptyLoadedTail": "lee las bandejas y ofrece registrar lo que encuentre.",
  "inv.totalStock": "Existencias totales",
  "inv.spools": "Bobinas",
  "inv.needsWeighing": "Por pesar",
  "inv.newSpool": "+ Nueva bobina",
  "inv.sync": "⟳ Sincronizar con la impresora",
  "inv.sealed": "Sellada",
  "inv.weighThis": "Pese esta bobina",

  // -- tira de sincronización --------------------------------------------------------
  "sync.dormantTitle": "No hay impresora conectada: nada que sincronizar.",
  "sync.dormantBody":
    "El registro lee las bandejas a través de la integración de Bambu Lab. Una vez " +
    "configurada, recargue Filament Ledger y este botón informará de bandejas reales.",
  "sync.noTraysTitle": "La impresora no informó de ninguna bandeja utilizable ahora mismo.",
  "sync.noTraysBody":
    "Una bandeja cuyo sensor no está disponible se omite; nunca se supone vacía. " +
    "Inténtelo de nuevo cuando la impresora sea accesible.",
  "sync.doneTitle": "Sincronizado con la impresora",
  "sync.slot": "Bandeja [[slot]]",
  "sync.empty": "vacía",
  "sync.mounted": "montada",
  "sync.detected": "detectada — se deja como está, el montaje automático está desactivado",
  "sync.noTag": "ocupada, etiqueta ilegible — nada automático es posible",
  "sync.noTagHints": "ocupada, etiqueta ilegible — nada automático es posible ([[hints]])",
  "sync.ambiguous":
    "dos bobinas comparten la etiqueta [[tag]] — monte la correcta a mano, el sistema no " +
    "elegirá",
  "sync.unknownTag": "etiqueta desconocida [[tag]]",
  "sync.unknownTagHints": "etiqueta desconocida [[tag]] · [[hints]]",
  "sync.notInInventory": "no está en el inventario",
  "sync.register": "Registrar…",

  // -- AMS ---------------------------------------------------------------------------
  "ams.slot": "Bandeja [[slot]]",
  "ams.empty": "Vacía",
  "ams.note":
    "Todavía no hay impresora conectada. Las bandejas se asignan a mano: montar no " +
    "registra ningún movimiento, porque mover una bobina no consume filamento.",

  // -- historial global --------------------------------------------------------------
  "history.emptyTitle": "Todavía no hay movimientos.",
  "history.emptyBody":
    "Cada gramo que entra o sale de cualquier bobina aparece aquí, del más reciente al más " +
    "antiguo: impresiones, correcciones y descartes en un mismo registro. Registre una " +
    "bobina y su saldo inicial será la primera fila.",
  "history.emptyFoot": "Nada de esto puede editarse jamás. Una corrección es una fila nueva.",
  "history.heading": "Todos los movimientos",
  "history.colWhen": "Cuándo",
  "history.colSpool": "Bobina",
  "history.colEntry": "Entrada",
  "history.colAmount": "Cantidad",
  "history.colSource": "Origen",
  "history.colCorrect": "Corregir",
  "history.colBalance": "Saldo",
  "history.foot":
    "Las [[count]] entradas más recientes, con todas las bobinas juntas. Para ver el saldo " +
    "acumulado detrás de cualquier fila, abra su bobina: un saldo solo se deriva dentro " +
    "del historial de una bobina.",
  "history.confirmed": "confirmado",
  "history.auto": "automático",
  "history.deleted": "eliminada",
  "history.reassignTitle": "Mover este cargo a otra bobina",
  "history.voidTitle": "Eliminar esta entrada y devolver los gramos",

  // -- la fila de filtros del historial (docs/06 §6.6) -------------------------------
  "history.filterSearch": "Nota o impresión",
  "history.filterSearchPlaceholder": "Buscar…",
  "history.filterSearchHelp":
    "Busca en la nota escrita en una entrada y en el nombre de la impresión de la que " +
    "proviene. No busca el nombre de la bobina —eso lo acotan los colores— ni la etiqueta " +
    "de la propia entrada.",
  "history.filterFrom": "Desde",
  "history.filterTo": "Hasta",
  "history.filterAmount": "Gramos movidos",
  "history.filterAmountHelp":
    "Cuánto filamento se movió, en cualquier sentido: una impresión que gastó 84 g cumple " +
    "con al menos 50 g, y una corrección que devolvió 84 g también.",
  "history.filterAtLeast": "al menos",
  "history.filterAtMost": "como máximo",
  "history.filterColour": "Color",
  "history.filterColourHelp":
    "Entradas de bobinas de los colores que elija. Puede elegir varios: los negros y los " +
    "grises son una sola pregunta, no dos.",
  "history.filterColourOne": "Filamento [[colour]]",
  "history.filterClear": "Quitar filtros",
  "history.filterToggle": "Filtros",
  "history.filterActive": "Acotado por [[count]] de estos controles",
  "history.noMatchTitle": "Nada coincide con estos filtros.",
  "history.noMatchBody":
    "El registro conserva todas las entradas que tenía hace un momento; lo que está vacío " +
    "es esta porción. Amplíe una fecha, quite un color o quite los filtros para volver a " +
    "verlo entero.",
  "history.footFiltered":
    "[[count]] entradas coinciden con estos filtros, de la más reciente a la más antigua. " +
    "El límite se aplica a lo que coincidió, así que ampliar un filtro puede devolver a la " +
    "vista entradas más antiguas.",

  // -- estadísticas (docs/06 §6.7, docs/15 §15.6) ------------------------------------
  "stats.periodLabel": "Período",
  "stats.period30d": "30 días",
  "stats.period90d": "90 días",
  "stats.periodall": "Todo el registro",
  "stats.consumed": "Impreso",
  "stats.wasted": "Desperdiciado",
  "stats.printsFinished": "Impresiones terminadas",
  "stats.reviewsResolved": "Revisiones resueltas",
  "stats.printTime": "Tiempo de impresión",
  "stats.printTimeAverage": "Impresión promedio",
  "stats.printTimeAcross":
    "Medido sobre [[count]] impresiones que registraron inicio y final: no es una " +
    "estimación, y no incluye todos los trabajos del período.",
  "stats.duration": "[[hours]] h [[minutes]] min",
  "stats.durationMinutes": "[[minutes]] min",
  "stats.byColour": "Filamento por color",
  "stats.byMaterial": "Filamento por material",
  "stats.outcomes": "Cómo terminaron las impresiones",
  "stats.outcomeFinished": "terminadas",
  "stats.outcomeCancelled": "canceladas",
  "stats.outcomeFailed": "fallidas",
  "stats.reviewsApproved": "aprobadas",
  "stats.reviewsDismissed": "descartadas",
  "stats.reviewsHeading": "Decisiones que tomó",
  "stats.topPrints": "Impresiones más grandes",
  "stats.colPrint": "Impresión",
  "stats.colWhen": "Inicio",
  "stats.colFilament": "Filamento",
  "stats.noConsumption": "En este período no salió filamento de ninguna bobina.",
  "stats.noTopPrints":
    "Todavía no hay consumo de este período asociado a una impresión concreta.",
  "stats.noOutcomes": "En este período no terminó ninguna impresión.",
  "stats.noReviews": "En este período no se resolvió ninguna revisión.",
  "stats.emptyTitle": "Todavía no hay nada que contar.",
  "stats.emptyBody":
    "Esta página suma lo que el registro ya contiene: cuánto filamento usaron sus " +
    "impresiones, cuánto se descartó, qué colores y materiales se gastan más rápido y cómo " +
    "terminaron las impresiones. Se completa sola a medida que imprime: aquí no se escribe " +
    "nada a mano.",
  "stats.emptyFoot":
    "Pruebe <b>Todo el registro</b> si ha impreso antes pero no últimamente. Una página " +
    "vacía significa que el registro no tiene nada en este período, nunca que un dato no " +
    "se haya podido cargar.",
  "stats.foot":
    "Calculado a partir del registro, nunca estimado. Una bobina que eliminó no cuenta en " +
    "nada, una entrada eliminada tampoco, y un descarte cuenta como desperdicio y no como " +
    "impresión: las mismas reglas que sigue el resto del panel.",

  // -- revisión ----------------------------------------------------------------------
  "review.emptyTitle": "Nada que revisar.",
  "review.emptyBody":
    "Las impresiones canceladas y fallidas aparecerán aquí para que confirme cuánto " +
    "filamento consumieron. Nunca se descuenta nada por ellas hasta que usted lo diga.",
  "review.emptyFoot":
    "Esta cola se llena una vez conectada la impresora. Hasta entonces, cada movimiento " +
    "del registro es uno que introdujo usted.",
  "review.pending": "[[count]] pendientes",
  "review.completed": "completada",
  "review.stoppedAtLayer": "se detuvo en la capa [[layer]] de [[total]]",
  "review.stoppedAtLayerPct": "se detuvo en la capa [[layer]] de [[total]] ([[pct]] %)",
  "review.stoppedAtPct": "se detuvo al [[pct]] %",
  "review.printerError": "Error de la impresora",
  "review.rawErrorTitle": "print_error sin procesar [[code]]",
  "review.printerReported": "la impresora informó &quot;[[state]]&quot;",
  "review.noDataTitle": "⛔ Sin datos de consumo — la impresora nunca los informó",
  "review.noDataBody": "No se ha descontado nada por esta impresión.",
  "review.total": "total <b>[[grams]]</b> g",
  "review.weighedSpools": "⚖ Pesé las bobinas:",
  "review.weighedWaste": "⚖ Pesé el desperdicio:",
  "review.distribute": "Repartir",
  "review.approve": "✓ Aprobar",
  "review.unknownSpool": "Bobina desconocida",
  "review.whichSpool": "¿qué bobina había en esta bandeja?",
  "review.chooseSpool": "Elegir bobina…",
  "review.addSpool": "+ Añadir bobina",
  "review.loadRest": "Cargar el resto",
  "review.loadRestTitle": "Cargar a esta bobina todo lo que le queda a la bandeja",
  "review.dropChargeTitle": "Quitar esta bobina de la bandeja",
  "review.remaining": "quedan [[grams]] g por cargar",
  "review.overCharged": "[[grams]] g más de lo que usó esta bandeja",
  "review.remainderHint":
    "Aprobar está desactivado hasta que cada gramo de [[slots]] esté cargado a una bobina.",
  "review.slotWord": "la bandeja [[slot]]",
  "review.blockedHint":
    "Aprobar está desactivado hasta que [[slots]] tenga una bobina, o su cantidad sea 0.",
  "review.invalidAmounts": "Las cantidades deben ser números cero o positivos.",

  // -- detalle de la bobina ----------------------------------------------------------
  "detail.back": "← Todas las bobinas",
  "detail.ofOpening": "g de [[opening]] g",
  "detail.deletedNote":
    "Esta bobina está en la papelera: se trata como si nunca se hubiera registrado y no " +
    "cuenta en ningún sitio. Su historial está debajo, entero e intacto, y restaurarla " +
    "devuelve ambas cosas.",
  "detail.restoreSpool": "Restaurar esta bobina",
  "detail.weigh": "Pesar",
  "detail.adjust": "Ajustar",
  "detail.edit": "Editar",
  "detail.finish": "Marcar como acabada",
  "detail.finishHelp":
    "El carrete salió vacío de la impresora. El saldo se fija en 0 g y la diferencia se " +
    "registra como una conciliación, de modo que la desviación queda a la vista en lugar " +
    "de perderse.",
  "detail.remove": "Quitar…",
  "detail.removeHelp":
    "¿La tiró a la basura o se registró por error? El paso siguiente pregunta cuál de las " +
    "dos, porque son hechos distintos.",
  "detail.heading": "Historial de movimientos",
  "detail.foot":
    "Leído de abajo arriba es una derivación, no una afirmación. Nada de lo anterior puede " +
    "editarse: una corrección es una fila nueva. Las entradas eliminadas siguen aquí, " +
    "tachadas, junto a la fila que devolvió sus gramos: esta es la única vista que no " +
    "oculta nada, porque es la vista que demuestra el total.",

  // -- papelera ----------------------------------------------------------------------
  "trash.emptyTitle": "La papelera está vacía.",
  "trash.emptyBody":
    "Las bobinas y las entradas de historial eliminadas esperan aquí, y todo puede " +
    "restaurarse. Nada del registro desaparece de verdad: una eliminación es una entrada " +
    "más, no una menos.",
  "trash.spoolsHeading": "Bobinas",
  "trash.spoolsBody":
    "Registradas por error, así que no cuentan en ningún sitio. Restaurar devuelve cada " +
    "una al almacenamiento — su bandeja anterior quedó libre y no se recupera — y su " +
    "historial vuelve con ella.",
  "trash.spoolMeta": "[[material]] · [[balance]] g · [[count]] entradas · eliminada [[when]]",
  "trash.movementsHeading": "Entradas del historial",
  "trash.movementsBody":
    "Eliminadas de las vistas que consulta a diario, nunca del registro. La entrada y la " +
    "fila que devolvió sus gramos siguen ahí, en el historial de su propia bobina.",
  "trash.movementMeta": "[[label]] · <b>[[grams]] g</b> [[direction]] · eliminada [[when]]",
  "trash.movementMetaReason":
    "[[label]] · <b>[[grams]] g</b> [[direction]] · eliminada [[when]] · [[reason]]",
  "trash.returned": "devueltos",
  "trash.removed": "retirados",
  "trash.noRestitution":
    "no se devolvió nada al eliminarla; el registro la sigue contando",
  "trash.spoolDeleted": "restaure primero la bobina de esta entrada",
  "trash.spoolDiscarded": "la bobina de esta entrada fue descartada: no hay de dónde descontar",

  // -- diálogo: registrar ------------------------------------------------------------
  "dlg.registerTitle": "Registrar una bobina",
  "dlg.material": "Material",
  "dlg.materialOther": "Nombre si es OTHER",
  "dlg.materialOtherPlaceholder": "Nylon-X",
  "dlg.colour": "Color",
  "dlg.openingWeight": "Peso inicial (g)",
  "dlg.coreWeight": "Peso del carrete vacío (g)",
  "dlg.coreWeightHelp":
    "Una balanza pesa la bobina entera. El registro resta este valor por usted.",
  "dlg.vendor": "Fabricante",
  "dlg.vendorPlaceholder": "Bambu Lab",
  "dlg.label": "Etiqueta",
  "dlg.labelPlaceholder": "Estante B",
  "dlg.tagFromSlot":
    "Se adjuntará la etiqueta [[tag]] de la bandeja [[slot]], de modo que la próxima " +
    "sincronización monte esta bobina por sí sola.",
  "dlg.register": "Registrar",

  // -- diálogo: editar ---------------------------------------------------------------
  "dlg.editTitle": "Editar bobina",
  "dlg.editClearNote":
    "Un campo Fabricante o Etiqueta vaciado conserva su valor actual: la etiqueta RFID de " +
    "abajo es el único campo que este diálogo puede borrar.",
  "dlg.tag": "Etiqueta RFID",
  "dlg.tagDetected":
    "Adjuntada por la impresora — la edición está desactivada para que la etiqueta " +
    "coincida siempre con la bobina física.",
  "dlg.tagPlaceholder": "ninguna",
  "dlg.tagClear": "Borrar",
  "dlg.tagYours": "Suya para cambiarla — vaciar el campo elimina la etiqueta.",
  "dlg.tagNone":
    "Aquí se puede asignar una etiqueta a una bobina; una etiqueta escrita aquí sigue " +
    "siendo suya para cambiarla.",
  "dlg.tagDuplicate": "Esta etiqueta pertenece a otra bobina a propósito",
  "dlg.correctHeading": "Corregir el peso",
  "dlg.correctBody":
    "Esto escribe un movimiento en el historial: la edición en sí nunca toca el saldo.",
  "dlg.setRemaining": "Fijar el filamento restante en (g)",
  "dlg.setRemainingHelp": "Filamento neto, sin el carrete. Se registra como conciliación.",
  "dlg.addRemove": "Añadir / quitar (g)",
  "dlg.addRemoveHelp": "En negativo resta, en positivo suma. Se registra como ajuste.",
  "dlg.adjustReason": "Motivo del ajuste",
  "dlg.adjustReasonHelp":
    "Obligatorio para un ajuste. Uno sin explicación es indistinguible de un error.",
  "dlg.correctNothing": "Deje ambos vacíos y no se escribirá nada en el historial.",
  "dlg.correctReconcile":
    "Registra una conciliación de [[delta]] g — de [[from]] g a [[to]] g.",
  "dlg.correctAdjust": "Registra un ajuste de [[delta]] g — el saldo pasa a [[after]] g.",
  "dlg.editCorrectionNote": "Corregido desde el diálogo de edición",

  // -- diálogo: pesar ----------------------------------------------------------------
  "dlg.weighTitle": "Pesar bobina",
  "dlg.weighBody": "Ponga la bobina entera en una balanza de cocina.",
  "dlg.measured": "Peso medido (g)",
  "dlg.includesCore": "Incluye el carrete ([[core]] g)",
  "dlg.weighFoot": "Esto se registra como una corrección. Nada de su historial cambia.",

  // -- diálogo: marcar una bobina como acabada ---------------------------------------
  "dlg.finishTitle": "¿Marcar [[name]] como acabada?",
  "dlg.finishSays":
    "El registro todavía dice que quedan <b>[[grams]] g</b>. Registrar el carrete vacío " +
    "escribe una conciliación de <b>[[delta]] g</b>: la desviación que han acumulado todas " +
    "las estimaciones desde la última vez que se pesó esta bobina.",
  "dlg.finishFoot":
    "Nada se cuenta como desperdicio y nada se carga a una impresión: usted está " +
    "declarando una medición, y la diferencia es el propio error del sistema, registrado " +
    "donde puede leerse. La bobina permanece en el inventario con 0 g hasta que la quite.",
  "dlg.finishConfirm": "Registrar bobina vacía",
  "dlg.finishNote": "Marcada como acabada — el carrete salió vacío",

  // -- diálogo: acciones de la bobina ------------------------------------------------
  "dlg.actionsBalance": "quedan [[grams]] g · [[state]]",

  // -- diálogo: ajustar --------------------------------------------------------------
  "dlg.adjustTitle": "Ajustar",
  "dlg.amount": "Cantidad (g)",
  "dlg.amountHelp": "En negativo resta, en positivo suma.",
  "dlg.adjustFoot":
    "El motivo es obligatorio. Un ajuste sin explicación es indistinguible de un error.",

  // -- diálogo: descartar ------------------------------------------------------------
  "dlg.discardTitle": "Descartar",
  "dlg.discardWhat": "Qué",
  "dlg.discardPartial": "Parte de esta bobina",
  "dlg.discardWhole": "La bobina entera",
  "dlg.discardAmount": "Cantidad (g), si es parcial",
  "dlg.discardReasonPlaceholder": "tramo enredado",

  // -- diálogo: montar ---------------------------------------------------------------
  "dlg.mountTitle": "Montar en la bandeja [[slot]]",
  "dlg.mountNone": "Todas las bobinas están ya montadas. Desmonte una primero.",
  "dlg.mountSpool": "Bobina",

  // -- diálogo: descartar revisión ---------------------------------------------------
  "dlg.dismissTitle": "¿Registrar consumo cero para esta impresión?",
  "dlg.dismissFoot":
    "Descartar es una decisión escrita en el historial, no una eliminación.",

  // -- diálogo: reasignar ------------------------------------------------------------
  "dlg.reassignTitle": "Reasignar este cargo",
  "dlg.reassignNone": "No hay ninguna otra bobina en el inventario a la que cargarlo.",
  "dlg.reassignSays":
    "Devolver <b>[[grams]] g</b> a <b>[[spool]]</b> y cargar <b>[[grams]] g</b> a la " +
    "bobina que elija. La entrada original permanece en el historial, marcada como " +
    "reasignada.",
  "dlg.reassignTo": "Cargarlo a",
  "dlg.reassignAmount": "Cuánto mover",
  "dlg.reassignAmountHelp":
    "Hasta [[grams]] g, el cargo completo. Mueva menos si la bobina se acabó a mitad de " +
    "camino y otra terminó la impresión.",
  "dlg.reassignFoot":
    "No hace falta motivo: el par nombra ambas bobinas y enlaza con la entrada que " +
    "corrige, así que se explica solo.",
  "dlg.reassign": "Reasignar",

  // -- diálogo: eliminar una entrada -------------------------------------------------
  "dlg.voidTitle": "¿Eliminar esta entrada?",
  "dlg.voidReturns": "Esto devuelve <b>[[grams]] g</b> a <b>[[spool]]</b>.",
  "dlg.voidRemoves": "Esto retira <b>[[grams]] g</b> de <b>[[spool]]</b>.",
  "dlg.voidFoot":
    "No se borra nada. La entrada deja las vistas que consulta a diario y espera en la " +
    "papelera; el registro anota la eliminación y la devolución de los gramos como dos " +
    "filas más.",
  "dlg.voidConfirm": "Eliminar entrada",
  "dlg.voidDeletedSpool":
    "<b>[[spool]]</b> está en la papelera, así que no hay a dónde devolver los " +
    "<b>[[grams]] g</b>.",
  "dlg.voidDiscardedSpool":
    "<b>[[spool]]</b> fue descartada, así que no hay a dónde devolver los " +
    "<b>[[grams]] g</b>.",
  "dlg.voidRestoreFirst": "Restaurar antes la bobina",
  "dlg.voidDiscardRoute":
    "El camino de vuelta para una bobina descartada es eliminar su entrada de descarte " +
    "total: eso devuelve el saldo y la bobina a la vez, en una sola operación.",
  "dlg.voidWhyNothing": "Por qué no vuelve nada",
  "dlg.voidWhyPlaceholder": "explique qué ocurrió",
  "dlg.voidNoRestitutionFoot":
    "Obligatorio aquí. La entrada sigue contando en el saldo de su bobina — solo cambian " +
    "las vistas — y una eliminación sin explicación se lee como un error seis meses " +
    "después. Esta no podrá restaurarse luego.",
  "dlg.voidNoRestitutionConfirm": "Eliminar sin devolver gramos",

  // -- diálogo: restaurar una entrada ------------------------------------------------
  "dlg.restoreTitle": "¿Restaurar esta entrada?",
  "dlg.restoreDeduct": "¿Descontar de nuevo <b>[[grams]] g</b> de <b>[[spool]]</b>?",
  "dlg.restoreAdd": "¿Añadir de nuevo <b>[[grams]] g</b> a <b>[[spool]]</b>?",
  "dlg.restoreFoot":
    "La entrada vuelve a su historial y el registro anota la restauración como una fila " +
    "más. Nada de lo ocurrido se reescribe.",

  // -- diálogo: quitar una bobina ----------------------------------------------------
  "dlg.intentTitle": "Quitar [[name]]",
  "dlg.intentAsk": "¿Qué le pasó realmente?",
  "dlg.intentThrewAway": "La tiré a la basura",
  "dlg.intentThrewAwayHelp":
    "Un hecho real: los [[grams]] g restantes cuentan como desperdicio en sus " +
    "estadísticas, y la bobina conserva su historial.",
  "dlg.intentMistake": "Se registró por error",
  "dlg.intentMistakeHelp":
    "La trata como si nunca se hubiera registrado: no cuenta en ningún sitio y puede " +
    "restaurarse desde la papelera.",

  // -- diálogo: el sujeto desapareció ------------------------------------------------
  "dlg.staleTitle": "Esa entrada ya no está donde estaba",
  "dlg.staleBody":
    "El registro cambió mientras esto estaba abierto. Ciérrelo e inténtelo de nuevo: no se " +
    "envió nada.",

  // -- pestaña impresora -------------------------------------------------------------
  "printer.dormantTitle": "No hay impresora conectada.",
  "printer.dormantBody":
    "El registro lee la impresora a través de la integración de Bambu Lab. Una vez " +
    "configurada, recargue Filament Ledger y esta pestaña mostrará lo que informa la " +
    "máquina.",
  "printer.dormantFoot":
    "Mientras tanto no se supone nada: ni indicador de carga, ni cuatro bandejas " +
    "inventadas.",
  "printer.refresh": "⟳ Actualizar",
  "printer.heading": "Impresora",
  "printer.status": "Estado",
  "printer.job": "Trabajo",
  "printer.progress": "Progreso",
  "printer.remaining": "Restante",
  "printer.layer": "Capa",
  "printer.layerOf": "[[current]] de [[total]]",
  "printer.online": "En línea",
  "printer.connection": "Conexión",
  "printer.activeTray": "Bandeja activa",
  "printer.yes": "sí",
  "printer.no": "no",
  "printer.errorHeading": "Error de la impresora",
  "printer.noError": "No se informa de ningún error.",
  "printer.hoursHeading": "Tiempo de impresión registrado aquí",
  "printer.hoursObserved":
    "Medido sobre [[count]] impresiones que este registro ha guardado desde el [[since]]. " +
    "No es el contador de vida de la máquina: la impresora no informa esa cifra, así que " +
    "el total empieza el día en que se instaló Filament Ledger, no el día en que se " +
    "estrenó la impresora.",
  "printer.traysHeading": "Bandejas",
  "printer.trayLedger": "registro: [[spool]]",
  "printer.trayLedgerEmpty": "registro: nada montado",
  "printer.noTrays": "La impresora no informó de ninguna bandeja utilizable ahora mismo.",
  "printer.pendingSensors":
    "Todavía no se leen el estado en línea, el modo de conexión ni la bandeja activa. Sus " +
    "claves de sensor deben confirmarse en una impresora real antes de que este panel " +
    "afirme conocerlas: una clave que nadie ha verificado es una clave que falla en otro " +
    "idioma.",
  "printer.readOnly":
    "Solo lectura, y se actualiza únicamente cuando usted lo pide. Abrir esta pestaña y " +
    "pulsar Actualizar no cambian nada del registro: el botón Sincronizar del Inventario " +
    "es el que sí lo hace.",

  // -- pestaña ajustes ---------------------------------------------------------------
  "settings.heading": "Valores predeterminados",
  "settings.openingWeight": "Peso inicial predeterminado (g)",
  "settings.openingWeightHelp":
    "Lo que contiene una bobina nueva. Las de Bambu traen 1000 g.",
  "settings.coreWeight": "Peso predeterminado de la bobina vacía (g)",
  "settings.coreWeightHelp":
    "El carrete desnudo, sin filamento. Una balanza de cocina pesa la bobina entera, así " +
    "que el registro resta este valor por usted.",
  "settings.anomalyThreshold": "Marcar una conciliación que se desvíe más de (%)",
  "settings.anomalyThresholdHelp":
    "Cuando la balanza discrepa del registro por más de esto, algo falla de forma " +
    "sistemática más arriba y conviene revisarlo.",
  "settings.autoMount": "Montar bobinas automáticamente al detectar el RFID",
  "settings.autoMountHelp":
    "Cuando la impresora lee una etiqueta conocida, registrar esa bobina como montada en " +
    "esa bandeja.",
  "settings.save": "Guardar ajustes",
  "settings.reloadWarning": "Guardar recarga Filament Ledger: un segundo o dos.",
  "settings.saved": "Guardado. Filament Ledger se está recargando con estos valores.",
  "settings.readOnly":
    "Solo un administrador puede cambiarlos. Deciden cómo se comporta el registro para " +
    "toda la casa, así que aquí se muestran en solo lectura en lugar de ocultarse: una " +
    "pestaña que falta invita a pensar que algo está roto.",
  "settings.languageHeading": "Idioma",
  "settings.languageAuto": "Automático (seguir a Home Assistant)",
  "settings.languageEn": "English",
  "settings.languageEs": "Español",
  "settings.languageHelp":
    "Solo para este dispositivo. Cambia lo que dice este panel, no una sola cosa de lo que " +
    "el registro guarda: así dos personas pueden leer el mismo registro en dos idiomas.",
};

const TABLES = { en: EN, es: ES };

/**
 * Fill `[[name]]` tokens, escaping every value on the way in.
 *
 * A token with no matching parameter is left standing rather than blanked: a visible
 * `[[spool]]` on screen names its own bug, and a silent empty space does not.
 */
function substitute(template, params) {
  if (!params) return template;
  return template.replace(/\[\[(\w+)\]\]/g, (token, name) =>
    Object.hasOwn(params, name) ? esc(params[name]) : token,
  );
}

/** The manual override, or `null` for auto. Storage can be denied; that reads as auto. */
export function readLanguageOverride() {
  try {
    const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    return SUPPORTED_LANGUAGES.includes(stored) ? stored : null;
  } catch {
    return null;
  }
}

/** Persist the override, or clear it with a falsy value. Failure is not worth an error. */
export function writeLanguageOverride(language) {
  try {
    if (SUPPORTED_LANGUAGES.includes(language)) {
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
    } else {
      window.localStorage.removeItem(LANGUAGE_STORAGE_KEY);
    }
  } catch {
    // A panel that cannot remember the choice still has to render in it.
  }
}

/**
 * Which language this panel speaks: the override, else the profile, else English.
 *
 * The profile match is by prefix, so `es-419` and `es-ES` both land on `es` — a regional
 * variant we do not carry is far closer to the base language than to English.
 */
export function resolveLanguage(hass) {
  const override = readLanguageOverride();
  if (override) return override;
  const profile = hass?.locale?.language ?? hass?.language ?? "";
  const prefix = String(profile).toLowerCase().split("-")[0];
  return SUPPORTED_LANGUAGES.includes(prefix) ? prefix : DEFAULT_LANGUAGE;
}

/**
 * A `t(key, params)` bound to one language.
 *
 * The result is markup-ready: parameters are already escaped, and the template is a
 * constant from this file. Call sites insert it directly and never wrap it in `esc()`.
 */
export function translator(language) {
  const table = TABLES[language] ?? TABLES[DEFAULT_LANGUAGE];
  const fallback = TABLES[DEFAULT_LANGUAGE];
  return (key, params) => {
    const template = table[key] ?? fallback[key];
    // Missing from both tables is a typo in the panel, not a translation gap — and the
    // key on screen is the fastest way to find it. EN is complete by construction.
    return template === undefined ? key : substitute(template, params);
  };
}
