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
export const SUPPORTED_LANGUAGES = ["en", "es", "tr"];
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
  "registration.retryConflict": "This request already saved different inputs. Retry the original draft, or explicitly cancel before starting a new registration.",
  "registration.invalidQuantity": "Check the quantity fields. Net filament must be positive and at most 10000 g. Gross must exceed the non-negative reel weight, and any supplied net must match.",
  "registration.tagConflict": "This tag is already in your inventory. Confirm a shared tag only if this is a separate physical spool.",
  "registration.saveFailed": "Could not confirm the save. Your draft and request ID are retained; retry to check the original result.",
  "position.conflict": "The spool or destination changed. Review the current assignment before confirming again. Your saved filament remains available.",
  "registration.colourStatus": "Colour information",
  "position.notReported": "Not reported · no filament assigned",
  "position.stale": "Printer unavailable · last report may be stale",
  "navigation.more": "More",
  "navigation.task": "Filament task",
  "notifications.title": "Low-stock automations",
  "notifications.admin": "An administrator can create or manage native Home Assistant automations. Inventory remains available to everyone.",
  "notifications.assetsUnavailable": "The local notification blueprint is unavailable. Reload the integration assets and try again.",
  "notifications.blueprintConflict": "An existing blueprint differs from this version. It was preserved. Review it in the native automation editor.",
  "notifications.defaultName": "Filament Ledger · low stock",
  "notifications.destinationMissing": "Choose an available notification destination. No supported destination is available? Configure one in Home Assistant first.",
  "notifications.scopeMissing": "Select at least one available spool, or explicitly choose all eligible spools. Missing targets are never replaced by all spools.",
  "notifications.configConflict": "This automation ID already has different configuration. It was preserved. Open the native editor to review it.",
  "notifications.readbackFailed": "Home Assistant accepted the request, but the saved rule could not be verified. Retry with this same draft.",
  "notifications.testMessage": "[TEST] Filament Ledger notification test. No stock quantity or production event changed.",
  "notifications.message": "{{ spool_name }}: {{ remaining_g }} g recorded remaining (threshold {{ threshold_g }} g). Confidence: {{ confidence }}. Weigh the spool if the recorded quantity is uncertain.",
  "notifications.disable": "Disable",
  "notifications.enable": "Enable",
  "notifications.nativeEdit": "Edit / delete in Home Assistant",
  "notifications.advanced": "Native configuration is authoritative. Advanced actions are preserved.",
  "notifications.all": "All eligible spools",
  "notifications.selected": "Selected physical spools",
  "notifications.scope": "Scope",
  "notifications.destination": "Notification destination",
  "notifications.chooseDestination": "Choose a destination",
  "notifications.unavailable": "Unavailable / unsupported required fields",
  "notifications.review": "Review notification",
  "notifications.reviewHelp": "This rule runs on future recorded crossings. Saving does not notify immediately. Test sends a separate TEST message only to the selected destination.",
  "notifications.test": "Send TEST",
  "notifications.save": "Save native automation",
  "notifications.phase.edit": "Choose scope and destination.",
  "notifications.phase.review": "Ready to save.",
  "notifications.phase.saving": "Saving and verifying configuration…",
  "notifications.phase.activating": "Configuration verified. Waiting for the automation entity; reload this view to check again if activation does not appear.",
  "notifications.phase.active": "Native automation activated. Its enabled state appears below.",
  "notifications.accepted": "Home Assistant accepted the TEST action. Check the destination separately to confirm receipt.",
  "notifications.setup": "Set up notification",
  "notifications.custom": "Prepare custom automation",
  "notifications.customEditor": "Open native blueprint editor",
  "notifications.intro": "One rule owner: Home Assistant. Thresholds update current status; only a future qualifying quantity crossing runs this blueprint.",
  "lowStock.threshold": "Low-stock threshold (g)",
  "lowStock.globalHelp": "Default for all spools without an override. Changing this updates status without sending a crossing notification.",
  "lowStock.override": "Per-spool threshold (g, optional)",
  "lowStock.inheritHelp": "Leave empty to inherit the household threshold.",
  "lowStock.inherited": "Household default",
  "lowStock.overridden": "Spool override",
  "lowStock.current": "Low stock",
  "colour.unknown": "Unknown colour",
  "colour.black": "Black",
  "colour.white": "White",
  "colour.grey": "Grey",
  "colour.red": "Red",
  "colour.orange": "Orange",
  "colour.yellow": "Yellow",
  "colour.green": "Green",
  "colour.blue": "Blue",
  "colour.purple": "Purple",
  "colour.pink": "Pink",
  "colour.brown": "Brown",
  "colour.beige": "Beige",
  "colour.custom": "Custom colour",
  "colour.picker": "Picker",
  "colour.code": "Colour code (optional)",
  "appearance.title": "Appearance",
  "appearance.mode": "Panel theme",
  "appearance.auto": "Automatic · device system",
  "appearance.light": "Light",
  "appearance.dark": "Dark",
  "appearance.help": "This device only. Automatic follows your system appearance.",
  "appearance.unsaved": "Applied for this session. This browser could not save your preference.",
  "discovery.filters": "Find filament",
  "discovery.search": "Name or brand",
  "discovery.material": "Material",
  "discovery.location": "Location",
  "discovery.sort": "Sort",
  "discovery.all": "All",
  "discovery.lowOnly": "Low stock only",
  "discovery.reset": "Reset filters",
  "discovery.count": "[[count]] filaments",
  "discovery.noResults": "No filament matches. Clear filters or change your search.",
  "discovery.location.STORAGE": "Storage",
  "discovery.location.AMS_SLOT": "AMS",
  "discovery.location.EXTERNAL_SPOOL": "External holder",
  "discovery.sort.remaining": "Smallest remainder first",
  "discovery.sort.name": "Name",
  "position.saved": "Filament saved — assignment incomplete",
  "position.savedHelp": "Your spool is available in Storage. Retry assignment to this position, or close and assign later. No second spool will be created.",
  "position.destination": "Destination: [[printer]] · [[position]]",
  "position.review": "Review current destination",
  "position.retry": "Assign saved filament",
  "registration.basis.LEGACY_UNSPECIFIED": "Legacy quantity — basis unspecified",
  "registration.basis.CONFIGURED_DEFAULT": "Configured starting default",
  "registration.basis.DETECTED_NOMINAL": "Detected nominal quantity",
  "registration.identity": "Filament identity",
  "registration.quantity": "Starting quantity",
  "registration.condition": "Condition",
  "registration.condition.new": "New / unopened",
  "registration.condition.partly_used": "Partly used / opened",
  "registration.basis": "Quantity basis",
  "registration.basis.DECLARED_NET": "Declared net filament",
  "registration.basis.MEASURED_NET": "Measured net filament",
  "registration.basis.MEASURED_GROSS": "Measured gross including reel",
  "registration.gross": "Gross weight (g)",
  "registration.preview": "Validated net filament: [[grams]] g",
  "registration.previewPending": "Checking quantity… Save becomes available after validation.",
  "registration.previewFailed": "Quantity could not be checked. Try again.",
  "registration.colour.true": "Known colour",
  "registration.colour.false": "Unknown colour",
  "registration.optionalTag": "Optional tag — no RFID required",
  "registration.duplicateTag": "This is a separate spool sharing an existing tag",
  "registration.markOpened": "Mark opened",
  // -- chrome ------------------------------------------------------------------------
  "app.title": "Filament Ledger",
  "app.loading": "Loading…",
  "app.adminBadge": "admin",

  // -- confidence (docs/02 §2.6) -----------------------------------------------------
  "conf.HIGH": "High",
  "conf.MEDIUM": "Medium",
  "conf.LOW": "Low",
  "conf.suffix": "[[level]] confidence",

  // -- why the badge reads what it reads (docs/06 §6.5) ------------------------------
  //
  // Two self-contained lines rather than one sentence with a hole in the middle: the
  // reason stands beside the chip, the anchor stands under it. Neither depends on the
  // other's grammar, so a translation is free to order each as its language wants.
  //
  // `conf.anchor.*` is keyed by the anchor's movement type, and the two keys are the two
  // different claims: *since you weighed it* and *since you registered it* are not the
  // same promise, and a reader is owed the one that is actually being made.
  "conf.why.estimate": "an estimate was approved [[when]]",
  "conf.why.drawn": "[[grams]] g drawn, [[pct]]% of this spool",
  "conf.why.nothing": "nothing drawn yet",
  "conf.anchor.RECONCILIATION": "Counting since you weighed it, [[when]]",
  "conf.anchor.OPENING_BALANCE": "Counting since you registered it, [[when]] · never weighed",
  "conf.anchor.NONE": "Nothing confirmed to count from",

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
  "tab.finished": "Finished",
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
  //
  // The `_ON` pair names the machine, and the panel reaches for it only once more than one
  // machine holds spools: *AMS slot 3* is a complete address in a one-printer household, and
  // a serial beside it on every card would be fifteen characters the reader looks past.
  "loc.AMS_SLOT": "AMS slot [[slot]]",
  "loc.AMS_SLOT_ON": "AMS slot [[slot]] on [[printer]]",
  "loc.EXTERNAL_SPOOL": "External spool",
  "loc.EXTERNAL_SPOOL_ON": "External spool on [[printer]]",
  // A dual-nozzle machine has a second holder, and it is a different place from the first.
  // Only the second is numbered: a machine with one holder has nothing to distinguish, so
  // every label already on screen for it stays exactly as it was.
  "loc.EXTERNAL_SPOOL_2": "External spool 2",
  "loc.EXTERNAL_SPOOL_2_ON": "External spool 2 on [[printer]]",
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
  "inv.allFinished":
    "Every spool here has run out. They are waiting in the Finished tab; register a new " +
    "one to keep printing.",

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
  "sync.noTag": "No RFID tag — register a spool of any brand",
  "sync.noTagHints": "No RFID tag — register a spool of any brand ([[hints]])",
  "sync.ambiguous":
    "two spools share tag [[tag]] — mount the right one by hand, the system will not pick",
  "sync.unknownTag": "unknown tag [[tag]]",
  "sync.unknownTagHints": "unknown tag [[tag]] · [[hints]]",
  "sync.notInInventory": "not in inventory",
  "sync.register": "Register…",

  // -- AMS ---------------------------------------------------------------------------
  "ams.slot": "Slot [[slot]]",
  // The heading over one AMS unit's four trays, shown only on a machine that has more than
  // one: with a single unit the number says nothing the reader did not already know.
  "ams.unit": "AMS [[ams]]",
  // The holder beside the AMS that feeds the extruder directly. Named by what it is,
  // because it has no slot number to be named by.
  "ams.external": "External spool",
  // A dual-nozzle machine has two, and *external spool* then names neither. Left and right
  // rather than one and two: the user is standing at the machine looking at two holders,
  // and upstream's own indexes (255 and 254) are no help to them at all.
  "ams.externalLeft": "External spool (left)",
  "ams.externalRight": "External spool (right)",
  "ams.empty": "Empty",
  // A holder the machine reports a reel on while the ledger has none there — the holder's
  // counterpart of `ams.chipless`, and the same invitation: the Mount button beside it is
  // how the user says which spool it is.
  "ams.occupied": "The printer reports a spool here",
  // A tray the machine reports occupied by a reel with no readable chip — third-party
  // filament. The ledger cannot identify it on its own; the Mount button beside it is
  // how the user says which spool it is.
  "ams.chipless": "Holding a spool with no chip",
  "ams.note":
    "Positions are assigned by hand — mounting records no movement, because moving a " +
    "spool consumes no filament.",
  // The heading over a tray space whose machine the ledger never recorded a name for — a
  // ledger migrated from single-printer days, before a second machine appeared. The spools
  // are real and they are where the ledger last saw them; only the machine is nameless.
  "ams.machineUnnamed": "Printer not identified",
  "ams.machineStale":
    "These spools are recorded on a machine this ledger is not currently following. " +
    "Nothing was moved for you, because which printer they are in was never recorded. " +
    "Mount each one onto the right machine when you next open its trays.",

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
  // The caption over one print's gathered rows. The name on it is data, escaped at the
  // call site; these two are the count beside it and the fallback for a print whose job
  // row can no longer say what it was called.
  "history.groupEntries": "[[count]] entries",
  "history.groupUnnamed": "Unnamed print",

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
  // Mid-sentence beside `review.slotWord`: the position with no slot number.
  "review.externalWord": "the external spool",
  // And the second holder of a dual-nozzle machine, which is a different position.
  "review.externalWord2": "the second external spool",
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

  // -- finished ----------------------------------------------------------------------
  "fin.emptyTitle": "Nothing has finished yet.",
  "fin.emptyBody":
    "Spools that run out of filament, and spools thrown away, move here — out of the " +
    "Inventory but never out of the ledger. Their history stays readable for as long " +
    "as you keep them.",
  "fin.body":
    "Run out, or thrown away. Open any spool to read the history that got it here; a " +
    "weighing that finds filament puts it straight back in the Inventory.",

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
  "dlg.mountExternalTitle": "Mount as external spool",
  "dlg.mountNone": "Every spool is already mounted. Unmount one first.",
  // The layered spool picker (mount dialog, and the reassign form's field). Two
  // sections: what can feed a print, and what the ledger says is spent. Spent spools
  // stay choosable — the reel that emptied mid-print is exactly the one a reassignment
  // names — but choosing one must read as deliberate, so it is labelled, never mixed in.
  "picker.inventory": "In inventory",
  "picker.spent": "Out of filament",
  // The picker's title when a review tray asks — the same question the row already
  // poses in lower case, asked in full over the choices.
  "picker.reviewTitle": "Which spool was in this tray?",
  "act.change": "Change",
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
  // *Position* rather than *tray*: a machine can be drawing through one of its holders,
  // and calling that a tray would be wrong on the one screen a user checks it from.
  "printer.activeFeed": "Active position",
  "printer.activeFeedAms": "AMS [[ams]] · [[slot]]",
  "printer.yes": "yes",
  "printer.no": "no",
  "printer.errorHeading": "Printer error",
  "printer.noError": "No error reported.",
  "printer.hoursHeading": "Print time recorded here",
  "printer.hoursObserved":
    "Measured across [[count]] prints this ledger has recorded since [[since]], on every " +
    "machine it follows. It is not any machine's lifetime counter — no printer reports " +
    "such a figure — so the total starts the day Filament Ledger was installed, not the " +
    "day any of the printers were.",
  "printer.trackingHeading": "Machines followed",
  "printer.trackingFollowing": "This ledger is following [[serials]].",
  "printer.trackingUnnamed":
    "[[count]] further machine or machines were found without a readable serial, and are " +
    "not followed. Two printers answering to one name would share a single set of trays, " +
    "so a machine that cannot be told apart from another is left alone rather than guessed " +
    "at. Please report this — every printer this project has seen reports its serial.",
  "printer.traysHeading": "Trays",
  "printer.trayLedger": "ledger: [[spool]]",
  "printer.trayLedgerEmpty": "ledger: nothing mounted",
  "printer.noTrays": "The printer reported no usable trays right now.",
  "printer.readOnly":
    "Opening this tab and pressing Refresh change nothing in the ledger. Use Register to import " +
    "a loaded spool of any brand, with or without RFID, then confirm its details and weight.",

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
  "settings.autoMount": "Assign detected spools automatically",
  "settings.autoMountHelp":
    "Assign known RFID spools and automatically registered chipless spools to their slots.",
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
  "settings.languageTr": "Türkçe",
  "settings.languageHelp":
    "This device only. It changes what this panel says, not a single thing the ledger " +
    "stores — so two people can read the same ledger in two languages.",
};

const ES = {
  "registration.retryConflict": "Esta solicitud ya guardó otros datos. Reintenta el borrador original o cancela explícitamente antes de iniciar otro registro.",
  "registration.invalidQuantity": "Revisa las cantidades. El filamento neto debe ser positivo y no superar 10000 g. El peso bruto debe superar el peso no negativo del carrete y coincidir con el neto indicado.",
  "registration.tagConflict": "Esta etiqueta ya está en el inventario. Confirma que se comparte solo si se trata de otra bobina física.",
  "registration.saveFailed": "No se pudo confirmar el guardado. Se conservan el borrador y el ID; reintenta para comprobar el resultado original.",
  "position.conflict": "La bobina o el destino ha cambiado. Revisa la asignación actual antes de confirmar de nuevo. El filamento guardado sigue disponible.",
  "registration.colourStatus": "Información de color",
  "position.notReported": "Sin datos · sin filamento asignado",
  "position.stale": "Impresora no disponible · último dato posiblemente obsoleto",
  "navigation.more": "Más",
  "navigation.task": "Tarea de filamento",
  "notifications.title": "Automatizaciones de existencias bajas",
  "notifications.admin": "Un administrador puede crear y gestionar automatizaciones nativas. El inventario sigue disponible para todos.",
  "notifications.assetsUnavailable": "El blueprint local no está disponible. Recarga los recursos de la integración.",
  "notifications.blueprintConflict": "El blueprint existente difiere de esta versión y se ha conservado. Revísalo en el editor nativo.",
  "notifications.defaultName": "Filament Ledger · existencias bajas",
  "notifications.destinationMissing": "Elige un destino de notificación disponible. Configura uno en Home Assistant si no hay ninguno compatible.",
  "notifications.scopeMissing": "Selecciona al menos una bobina disponible o elige explícitamente todas. Los destinos ausentes nunca amplían el alcance.",
  "notifications.configConflict": "Este ID ya tiene otra configuración y se ha conservado. Revísala en el editor nativo.",
  "notifications.readbackFailed": "Home Assistant aceptó la solicitud, pero no se pudo verificar la regla. Reintenta con este mismo borrador.",
  "notifications.testMessage": "[TEST] Prueba de Filament Ledger. No se ha cambiado ninguna cantidad ni generado un evento de producción.",
  "notifications.message": "{{ spool_name }}: quedan {{ remaining_g }} g registrados (umbral {{ threshold_g }} g). Confianza: {{ confidence }}. Pesa la bobina si la cantidad registrada es incierta.",
  "notifications.disable": "Desactivar",
  "notifications.enable": "Activar",
  "notifications.nativeEdit": "Editar / eliminar en Home Assistant",
  "notifications.advanced": "La configuración nativa es la referencia. Se conservan las acciones avanzadas.",
  "notifications.all": "Todas las bobinas elegibles",
  "notifications.selected": "Bobinas físicas seleccionadas",
  "notifications.scope": "Alcance",
  "notifications.destination": "Destino de notificación",
  "notifications.chooseDestination": "Elige un destino",
  "notifications.unavailable": "No disponible / campos obligatorios no compatibles",
  "notifications.review": "Revisar notificación",
  "notifications.reviewHelp": "Esta regla actúa en futuros cruces registrados. Guardarla no envía mensajes. La prueba solo envía un mensaje TEST al destino elegido.",
  "notifications.test": "Enviar TEST",
  "notifications.save": "Guardar automatización nativa",
  "notifications.phase.edit": "Elige alcance y destino.",
  "notifications.phase.review": "Lista para guardar.",
  "notifications.phase.saving": "Guardando y verificando…",
  "notifications.phase.activating": "Configuración verificada. Esperando la entidad de automatización; recarga esta vista para comprobarlo de nuevo.",
  "notifications.phase.active": "Automatización nativa activada. El estado real aparece debajo.",
  "notifications.accepted": "Home Assistant aceptó la acción TEST. Comprueba la recepción en el destino por separado.",
  "notifications.setup": "Configurar notificación",
  "notifications.custom": "Preparar automatización personalizada",
  "notifications.customEditor": "Abrir editor nativo del blueprint",
  "notifications.intro": "Home Assistant gestiona cada regla. Cambiar el umbral actualiza el estado; solo un futuro cruce de cantidad ejecuta este blueprint.",
  "lowStock.threshold": "Umbral de existencias bajas (g)",
  "lowStock.globalHelp": "Valor predeterminado para bobinas sin ajuste propio. Cambiarlo actualiza el estado sin enviar una notificación de cruce.",
  "lowStock.override": "Umbral de la bobina (g, opcional)",
  "lowStock.inheritHelp": "Déjalo vacío para usar el umbral general.",
  "lowStock.inherited": "Valor general",
  "lowStock.overridden": "Ajuste de la bobina",
  "lowStock.current": "Existencias bajas",
  "colour.unknown": "Color desconocido",
  "colour.black": "Negro",
  "colour.white": "Blanco",
  "colour.grey": "Gris",
  "colour.red": "Rojo",
  "colour.orange": "Naranja",
  "colour.yellow": "Amarillo",
  "colour.green": "Verde",
  "colour.blue": "Azul",
  "colour.purple": "Morado",
  "colour.pink": "Rosa",
  "colour.brown": "Marrón",
  "colour.beige": "Beis",
  "colour.custom": "Color personalizado",
  "colour.picker": "Selector",
  "colour.code": "Código de color (opcional)",
  "appearance.title": "Apariencia",
  "appearance.mode": "Tema del panel",
  "appearance.auto": "Automático · sistema del dispositivo",
  "appearance.light": "Claro",
  "appearance.dark": "Oscuro",
  "appearance.help": "Solo este dispositivo. Automático sigue la apariencia del sistema.",
  "appearance.unsaved": "Aplicado en esta sesión. El navegador no pudo guardar la preferencia.",
  "discovery.filters": "Buscar filamento",
  "discovery.search": "Nombre o marca",
  "discovery.material": "Material",
  "discovery.location": "Ubicación",
  "discovery.sort": "Orden",
  "discovery.all": "Todos",
  "discovery.lowOnly": "Solo existencias bajas",
  "discovery.reset": "Restablecer filtros",
  "discovery.count": "[[count]] filamentos",
  "discovery.noResults": "Sin resultados. Borra los filtros o cambia la búsqueda.",
  "discovery.location.STORAGE": "Almacenamiento",
  "discovery.location.AMS_SLOT": "AMS",
  "discovery.location.EXTERNAL_SPOOL": "Soporte externo",
  "discovery.sort.remaining": "Menor cantidad primero",
  "discovery.sort.name": "Nombre",
  "position.saved": "Filamento guardado — asignación incompleta",
  "position.savedHelp": "La bobina está en Almacenamiento. Reintenta la asignación o ciérrala para hacerlo después. No se creará otra bobina.",
  "position.destination": "Destino: [[printer]] · [[position]]",
  "position.review": "Revisar destino actual",
  "position.retry": "Asignar filamento guardado",
  "registration.basis.LEGACY_UNSPECIFIED": "Cantidad anterior — origen sin especificar",
  "registration.basis.CONFIGURED_DEFAULT": "Cantidad inicial configurada",
  "registration.basis.DETECTED_NOMINAL": "Cantidad nominal detectada",
  "registration.identity": "Identidad del filamento",
  "registration.quantity": "Cantidad inicial",
  "registration.condition": "Estado",
  "registration.condition.new": "Nuevo / sin abrir",
  "registration.condition.partly_used": "Usado / abierto",
  "registration.basis": "Origen de la cantidad",
  "registration.basis.DECLARED_NET": "Filamento neto declarado",
  "registration.basis.MEASURED_NET": "Filamento neto medido",
  "registration.basis.MEASURED_GROSS": "Peso bruto medido con bobina",
  "registration.gross": "Peso bruto (g)",
  "registration.preview": "Filamento neto validado: [[grams]] g",
  "registration.previewPending": "Comprobando cantidad… Guarda tras la validación.",
  "registration.previewFailed": "No se pudo comprobar. Inténtalo de nuevo.",
  "registration.colour.true": "Color conocido",
  "registration.colour.false": "Color desconocido",
  "registration.optionalTag": "Etiqueta opcional — no requiere RFID",
  "registration.duplicateTag": "Es otra bobina con una etiqueta compartida",
  "registration.markOpened": "Marcar como abierta",
  // -- chrome ------------------------------------------------------------------------
  "app.title": "Filament Ledger",
  "app.loading": "Cargando…",
  "app.adminBadge": "administrador",

  // -- confianza ---------------------------------------------------------------------
  "conf.HIGH": "Alta",
  "conf.MEDIUM": "Media",
  "conf.LOW": "Baja",
  "conf.suffix": "confianza [[level]]",

  // -- por qué la insignia dice lo que dice (docs/06 §6.5) ---------------------------
  "conf.why.estimate": "se aprobó una estimación [[when]]",
  "conf.why.drawn": "[[grams]] g consumidos, el [[pct]] % de esta bobina",
  "conf.why.nothing": "todavía no se ha consumido nada",
  "conf.anchor.RECONCILIATION": "Contando desde que la pesó, [[when]]",
  "conf.anchor.OPENING_BALANCE": "Contando desde que la registró, [[when]] · nunca pesada",
  "conf.anchor.NONE": "No hay ninguna medición confirmada desde la que contar",

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
  "tab.finished": "Terminadas",
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
  "loc.AMS_SLOT_ON": "Bandeja [[slot]] del AMS en [[printer]]",
  "loc.EXTERNAL_SPOOL": "Bobina externa",
  "loc.EXTERNAL_SPOOL_ON": "Bobina externa en [[printer]]",
  "loc.EXTERNAL_SPOOL_2": "Bobina externa 2",
  "loc.EXTERNAL_SPOOL_2_ON": "Bobina externa 2 en [[printer]]",
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
  "inv.allFinished":
    "Todas las bobinas de aquí se han agotado. Esperan en la pestaña Terminadas; " +
    "registre una nueva para seguir imprimiendo.",

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
  "sync.noTag": "Sin RFID: registre una bobina de cualquier marca",
  "sync.noTagHints": "Sin RFID: registre una bobina de cualquier marca ([[hints]])",
  "sync.ambiguous":
    "dos bobinas comparten la etiqueta [[tag]] — monte la correcta a mano, el sistema no " +
    "elegirá",
  "sync.unknownTag": "etiqueta desconocida [[tag]]",
  "sync.unknownTagHints": "etiqueta desconocida [[tag]] · [[hints]]",
  "sync.notInInventory": "no está en el inventario",
  "sync.register": "Registrar…",

  // -- AMS ---------------------------------------------------------------------------
  "ams.slot": "Bandeja [[slot]]",
  "ams.unit": "AMS [[ams]]",
  "ams.external": "Bobina externa",
  "ams.externalLeft": "Bobina externa (izquierda)",
  "ams.externalRight": "Bobina externa (derecha)",
  "ams.empty": "Vacía",
  "ams.occupied": "La impresora informa de una bobina aquí",
  "ams.chipless": "Hay una bobina sin chip",
  "ams.note":
    "Las posiciones se asignan a mano: montar no registra ningún movimiento, porque " +
    "mover una bobina no consume filamento.",
  "ams.machineUnnamed": "Impresora sin identificar",
  "ams.machineStale":
    "Estas bobinas están registradas en una máquina que este registro no sigue ahora " +
    "mismo. No se movió nada de forma automática, porque nunca se anotó en qué impresora " +
    "estaban. Monte cada una en la máquina correcta la próxima vez que abra sus bandejas.",

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
  "history.groupEntries": "[[count]] entradas",
  "history.groupUnnamed": "Impresión sin nombre",

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
  "review.externalWord": "la bobina externa",
  "review.externalWord2": "la segunda bobina externa",
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

  // -- terminadas --------------------------------------------------------------------
  "fin.emptyTitle": "Todavía no ha terminado ninguna.",
  "fin.emptyBody":
    "Las bobinas que agotan su filamento y las que se tiran a la basura pasan aquí: " +
    "fuera del inventario, pero nunca fuera del registro. Su historial sigue " +
    "disponible mientras las conserve.",
  "fin.body":
    "Agotadas o tiradas a la basura. Abra cualquier bobina para leer el historial que " +
    "la trajo hasta aquí; un pesaje que encuentre filamento la devuelve directamente " +
    "al inventario.",

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
  "dlg.mountExternalTitle": "Montar como bobina externa",
  "dlg.mountNone": "Todas las bobinas están ya montadas. Desmonte una primero.",
  "picker.inventory": "En inventario",
  "picker.spent": "Sin saldo",
  "picker.reviewTitle": "¿Qué bobina había en esta bandeja?",
  "act.change": "Cambiar",
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
  "printer.activeFeed": "Posición activa",
  "printer.activeFeedAms": "AMS [[ams]] · [[slot]]",
  "printer.yes": "sí",
  "printer.no": "no",
  "printer.errorHeading": "Error de la impresora",
  "printer.noError": "No se informa de ningún error.",
  "printer.hoursHeading": "Tiempo de impresión registrado aquí",
  "printer.hoursObserved":
    "Medido sobre [[count]] impresiones que este registro ha guardado desde el [[since]], " +
    "en todas las máquinas que sigue. No es el contador de vida de ninguna máquina: " +
    "ninguna impresora informa esa cifra, así que el total empieza el día en que se " +
    "instaló Filament Ledger, no el día en que se estrenó ninguna de las impresoras.",
  "printer.trackingHeading": "Máquinas seguidas",
  "printer.trackingFollowing": "Este registro sigue a [[serials]].",
  "printer.trackingUnnamed":
    "Se encontraron [[count]] máquinas más sin número de serie legible, y no se siguen. " +
    "Dos impresoras con un mismo nombre compartirían un único juego de bandejas, así que " +
    "una máquina que no puede distinguirse de otra se deja aparte en lugar de adivinarla. " +
    "Le agradeceríamos que lo informara: toda impresora vista por este proyecto informa " +
    "su número de serie.",
  "printer.traysHeading": "Bandejas",
  "printer.trayLedger": "registro: [[spool]]",
  "printer.trayLedgerEmpty": "registro: nada montado",
  "printer.noTrays": "La impresora no informó de ninguna bandeja utilizable ahora mismo.",
  "printer.readOnly":
    "Abrir esta pestaña y pulsar Actualizar no cambian el registro. Use Registrar para importar " +
    "una bobina de cualquier marca, con o sin RFID, y confirme sus datos y peso.",

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
  "settings.autoMount": "Asignar bobinas detectadas automáticamente",
  "settings.autoMountHelp":
    "Asignar bobinas RFID conocidas y bobinas sin RFID registradas automáticamente a sus bandejas.",
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
  "settings.languageTr": "Türkçe",
  "settings.languageHelp":
    "Solo para este dispositivo. Cambia lo que dice este panel, no una sola cosa de lo que " +
    "el registro guarda: así dos personas pueden leer el mismo registro en dos idiomas.",
};

const TR = {
  "registration.retryConflict": "Bu istek daha önce farklı bilgilerle kaydedildi. İlk taslakla yeniden deneyin veya yeni kayıt açmadan önce açıkça iptal edin.",
  "registration.invalidQuantity": "Miktar alanlarını kontrol edin. Net filament sıfırdan büyük ve en fazla 10000 g olmalı. Brüt ağırlık, sıfır veya üzerindeki boş makara ağırlığından büyük olmalı; girilen net miktar da hesapla eşleşmeli.",
  "registration.tagConflict": "Bu etiket zaten envanterinizde. Yalnızca ayrı bir fiziksel makara için ortak etiketi onaylayın.",
  "registration.saveFailed": "Kayıt doğrulanamadı. Taslağınız ve istek kimliği korundu; ilk işlemin sonucunu kontrol etmek için yeniden deneyin.",
  "position.conflict": "Makara veya hedef değişti. Yeniden onaylamadan önce mevcut atamayı kontrol edin. Kaydettiğiniz filament kullanılabilir durumda.",
  "registration.colourStatus": "Renk bilgisi",
  "position.notReported": "Bildirilmedi · filament atanmamış",
  "position.stale": "Yazıcıya ulaşılamıyor · son bilgi güncel olmayabilir",
  "navigation.more": "Diğer",
  "navigation.task": "Filament işlemi",
  "notifications.title": "Azalan stok otomasyonları",
  "notifications.admin": "Home Assistant otomasyonlarını bir yönetici oluşturabilir veya yönetebilir. Envanter herkese açık kalır.",
  "notifications.assetsUnavailable": "Yerel bildirim şablonu bulunamadı. Entegrasyon dosyalarını yeniden yükleyip tekrar deneyin.",
  "notifications.blueprintConflict": "Mevcut otomasyon şablonu bu sürümden farklı olduğu için korundu. Home Assistant otomasyon düzenleyicisinde inceleyin.",
  "notifications.defaultName": "Filament Ledger · azalan stok",
  "notifications.destinationMissing": "Kullanılabilir bir bildirim hedefi seçin. Desteklenen hedef yoksa önce Home Assistant'ta bir hedef yapılandırın.",
  "notifications.scopeMissing": "En az bir kullanılabilir makara seçin veya uygun tüm makaraları açıkça seçin. Eksik hedefler yerine tüm makaralar seçilmez.",
  "notifications.configConflict": "Bu otomasyon kimliğinde farklı bir yapılandırma var ve korundu. İncelemek için Home Assistant düzenleyicisini açın.",
  "notifications.readbackFailed": "Home Assistant isteği kabul etti ancak kaydedilen kural doğrulanamadı. Aynı taslakla yeniden deneyin.",
  "notifications.testMessage": "[TEST] Filament Ledger bildirim denemesi. Stok miktarı ve gerçek olaylar değiştirilmedi.",
  "notifications.message": "{{ spool_name }}: kayıtlı kalan miktar {{ remaining_g }} g (eşik {{ threshold_g }} g). Güven düzeyi: {{ confidence }}. Kayıtlı miktardan emin değilseniz makarayı tartın.",
  "notifications.disable": "Devre dışı bırak",
  "notifications.enable": "Etkinleştir",
  "notifications.nativeEdit": "Home Assistant'ta düzenle / sil",
  "notifications.advanced": "Home Assistant yapılandırması esas alınır. Gelişmiş eylemler korunur.",
  "notifications.all": "Uygun tüm makaralar",
  "notifications.selected": "Seçili fiziksel makaralar",
  "notifications.scope": "Kapsam",
  "notifications.destination": "Bildirim hedefi",
  "notifications.chooseDestination": "Hedef seçin",
  "notifications.unavailable": "Kullanılamıyor / desteklenmeyen zorunlu alanlar var",
  "notifications.review": "Bildirimi gözden geçir",
  "notifications.reviewHelp": "Bu kural gelecekte kaydedilen eşik geçişlerinde çalışır. Kaydetmek hemen bildirim göndermez. Deneme, yalnızca seçili hedefe ayrı bir TEST mesajı gönderir.",
  "notifications.test": "TEST gönder",
  "notifications.save": "Otomasyonu kaydet",
  "notifications.phase.edit": "Kapsamı ve hedefi seçin.",
  "notifications.phase.review": "Kaydetmeye hazır.",
  "notifications.phase.saving": "Yapılandırma kaydediliyor ve doğrulanıyor…",
  "notifications.phase.activating": "Yapılandırma doğrulandı. Otomasyon varlığı bekleniyor; etkinleşme görünmezse bu görünümü yeniden yükleyin.",
  "notifications.phase.active": "Home Assistant otomasyonu etkinleştirildi. Açık/kapalı durumu aşağıda gösteriliyor.",
  "notifications.accepted": "Home Assistant TEST eylemini kabul etti. Mesajın ulaştığını hedefte ayrıca kontrol edin.",
  "notifications.setup": "Bildirim oluştur",
  "notifications.custom": "Özel otomasyon hazırla",
  "notifications.customEditor": "Otomasyon şablonu düzenleyicisini aç",
  "notifications.intro": "Kuralları Home Assistant yönetir. Eşikler güncel durumu belirler; bu şablon yalnızca gelecekteki uygun miktar geçişlerinde çalışır.",
  "lowStock.threshold": "Azalan stok eşiği (g)",
  "lowStock.globalHelp": "Özel eşiği olmayan tüm makaralar için varsayılan değer. Değiştirmek durumu günceller; eşik geçişi bildirimi göndermez.",
  "lowStock.override": "Makaraya özel eşik (g, isteğe bağlı)",
  "lowStock.inheritHelp": "Ortak varsayılan eşiği kullanmak için boş bırakın.",
  "lowStock.inherited": "Ortak varsayılan",
  "lowStock.overridden": "Makaraya özel",
  "lowStock.current": "Stok az",
  "colour.unknown": "Bilinmeyen renk",
  "colour.black": "Siyah",
  "colour.white": "Beyaz",
  "colour.grey": "Gri",
  "colour.red": "Kırmızı",
  "colour.orange": "Turuncu",
  "colour.yellow": "Sarı",
  "colour.green": "Yeşil",
  "colour.blue": "Mavi",
  "colour.purple": "Mor",
  "colour.pink": "Pembe",
  "colour.brown": "Kahverengi",
  "colour.beige": "Bej",
  "colour.custom": "Özel renk",
  "colour.picker": "Seçici",
  "colour.code": "Renk kodu (isteğe bağlı)",
  "appearance.title": "Görünüm",
  "appearance.mode": "Panel teması",
  "appearance.auto": "Otomatik · cihaz sistemi",
  "appearance.light": "Aydınlık",
  "appearance.dark": "Karanlık",
  "appearance.help": "Yalnızca bu cihaz için. Otomatik seçim sistem temanızı izler.",
  "appearance.unsaved": "Bu oturum için uygulandı. Tarayıcı tercihinizi kaydedemedi.",
  "discovery.filters": "Filament bul",
  "discovery.search": "Ad veya marka",
  "discovery.material": "Malzeme",
  "discovery.location": "Konum",
  "discovery.sort": "Sıralama",
  "discovery.all": "Tümü",
  "discovery.lowOnly": "Yalnızca azalan stok",
  "discovery.reset": "Filtreleri sıfırla",
  "discovery.count": "[[count]] filament",
  "discovery.noResults": "Eşleşen filament yok. Filtreleri temizleyin veya aramanızı değiştirin.",
  "discovery.location.STORAGE": "Depo",
  "discovery.location.AMS_SLOT": "AMS",
  "discovery.location.EXTERNAL_SPOOL": "Harici makara tutucusu",
  "discovery.sort.remaining": "En az kalan önce",
  "discovery.sort.name": "Ad",
  "position.saved": "Filament kaydedildi — konum ataması tamamlanmadı",
  "position.savedHelp": "Makaranız depoda kullanılabilir. Bu konuma atamayı yeniden deneyin veya kapatıp daha sonra atayın. İkinci bir makara oluşturulmaz.",
  "position.destination": "Hedef: [[printer]] · [[position]]",
  "position.review": "Mevcut hedefi gözden geçir",
  "position.retry": "Kaydedilen filamenti ata",
  "registration.basis.LEGACY_UNSPECIFIED": "Eski kayıt — miktarın kaynağı belirtilmemiş",
  "registration.basis.CONFIGURED_DEFAULT": "Yapılandırılmış başlangıç varsayılanı",
  "registration.basis.DETECTED_NOMINAL": "Algılanan nominal miktar",
  "registration.identity": "Filament bilgileri",
  "registration.quantity": "Başlangıç miktarı",
  "registration.condition": "Durum",
  "registration.condition.new": "Yeni / açılmamış",
  "registration.condition.partly_used": "Kısmen kullanılmış / açılmış",
  "registration.basis": "Miktarın kaynağı",
  "registration.basis.DECLARED_NET": "Beyan edilen net filament",
  "registration.basis.MEASURED_NET": "Tartılan net filament",
  "registration.basis.MEASURED_GROSS": "Makara dahil tartılan brüt ağırlık",
  "registration.gross": "Brüt ağırlık (g)",
  "registration.preview": "Doğrulanan net filament: [[grams]] g",
  "registration.previewPending": "Miktar kontrol ediliyor… Doğrulamadan sonra kaydedebilirsiniz.",
  "registration.previewFailed": "Miktar kontrol edilemedi. Yeniden deneyin.",
  "registration.colour.true": "Bilinen renk",
  "registration.colour.false": "Bilinmeyen renk",
  "registration.optionalTag": "İsteğe bağlı etiket — RFID gerekmez",
  "registration.duplicateTag": "Bu, mevcut bir etiketi paylaşan ayrı bir makara",
  "registration.markOpened": "Açıldı olarak işaretle",
  "app.title": "Filament Ledger",
  "app.loading": "Yükleniyor…",
  "app.adminBadge": "yönetici",
  "conf.HIGH": "Yüksek",
  "conf.MEDIUM": "Orta",
  "conf.LOW": "Düşük",
  "conf.suffix": "[[level]] güven düzeyi",
  "conf.why.estimate": "bir tahmin onaylandı: [[when]]",
  "conf.why.drawn": "[[grams]] g kullanıldı; bu makaranın %[[pct]] kadarı",
  "conf.why.nothing": "henüz kullanılmadı",
  "conf.anchor.RECONCILIATION": "Son tartımdan beri hesaplanıyor: [[when]]",
  "conf.anchor.OPENING_BALANCE": "Kayıttan beri hesaplanıyor: [[when]] · hiç tartılmadı",
  "conf.anchor.NONE": "Hesaba temel olacak doğrulanmış miktar yok",
  "est.LINEAR_PROGRESS": "İlerleme oranından tahmin edildi · yaklaşık",
  "est.NONE": "Yazıcı tarafından bildirildi · tahmin değil",
  "tab.inventory": "Envanter",
  "tab.history": "Geçmiş",
  "tab.stats": "İstatistikler",
  "tab.review": "İnceleme",
  "tab.ams": "AMS",
  "tab.printer": "Yazıcı",
  "tab.finished": "Bitenler",
  "tab.trash": "Çöp kutusu",
  "tab.settings": "Ayarlar",
  "hist.PRINT_CONSUMPTION": "Baskı",
  "hist.ESTIMATED_CONSUMPTION": "Tahmin (onaylandı)",
  "hist.MANUAL_ADJUSTMENT": "Düzeltme",
  "hist.RECONCILIATION": "Tartım düzeltmesi",
  "hist.OPENING_BALANCE": "Başlangıç bakiyesi",
  "hist.DISCARD": "Atık",
  "hist.PURGE_WASTE": "Temizleme atığı",
  "hist.REASSIGNMENT": "Yeniden atandı",
  "hist.VOID_REVERSAL": "Silindi (iade edildi)",
  "hist.REINSTATEMENT": "Geri yüklendi",
  "mv.PRINT_CONSUMPTION": "Baskı",
  "mv.ESTIMATED_CONSUMPTION": "Tahmini tüketim",
  "mv.MANUAL_ADJUSTMENT": "Düzeltme",
  "mv.RECONCILIATION": "Tartım düzeltmesi",
  "mv.OPENING_BALANCE": "Başlangıç bakiyesi",
  "mv.DISCARD": "Atık",
  "mv.PURGE_WASTE": "Temizleme atığı",
  "mv.REASSIGNMENT": "Yeniden atama",
  "mv.VOID_REVERSAL": "Silinen kayıt — iade edildi",
  "mv.REINSTATEMENT": "Geri yüklenen kayıt",
  "src.USER_CONFIRMED": "sizin onayınızla",
  "src.AUTOMATIC": "otomatik",
  "loc.AMS_SLOT": "AMS yuvası [[slot]]",
  "loc.AMS_SLOT_ON": "[[printer]] · AMS yuvası [[slot]]",
  "loc.EXTERNAL_SPOOL": "Harici makara",
  "loc.EXTERNAL_SPOOL_ON": "[[printer]] · harici makara",
  "loc.EXTERNAL_SPOOL_2": "Harici makara 2",
  "loc.EXTERNAL_SPOOL_2_ON": "[[printer]] · harici makara 2",
  "loc.STORAGE": "Depo",
  "state.SEALED": "açılmamış",
  "state.ACTIVE": "kullanımda",
  "state.DEPLETED": "bitmiş",
  "state.DISCARDED": "atılmış",
  "state.DELETED": "silinmiş",
  "time.today": "Bugün [[time]]",
  "time.yesterday": "Dün",
  "time.daysAgo": "[[days]] gün önce",
  "act.cancel": "İptal",
  "act.dismiss": "Kapat",
  "act.open": "Aç",
  "act.restore": "Geri yükle",
  "act.record": "Kaydet",
  "act.save": "Kaydet",
  "act.mount": "Tak",
  "act.unmount": "Çıkar",
  "act.discard": "Atık kaydet",
  "act.note": "Not",
  "act.reason": "Gerekçe",
  "act.optional": "isteğe bağlı",
  "act.why": "gerekçe",
  "act.and": " ve ",
  "act.spoolActions": "Makara işlemleri",
  "inv.emptyTitle": "Henüz makara yok.",
  "inv.emptyBody": "Sahip olduğunuz filamenti kaydedin; sistem kullanılan her gramı takip etmeye başlasın.",
  "inv.emptyCta": "İlk makaranızı kaydedin",
  "inv.emptyLoaded": "Yazıcıda filament takılı mı?",
  "inv.emptyLoadedTail": "yuvaları okur ve bulduğu filamenti kaydetmenizi sağlar.",
  "inv.totalStock": "Toplam stok",
  "inv.spools": "Makaralar",
  "inv.needsWeighing": "Tartım gerekli",
  "inv.newSpool": "+ Yeni makara",
  "inv.sync": "⟳ Yazıcıyla eşitle",
  "inv.sealed": "Açılmamış",
  "inv.weighThis": "Bu makarayı tartın",
  "inv.allFinished": "Buradaki tüm makaralar bitti ve Bitenler sekmesine taşındı. Baskıya devam etmek için yeni bir makara kaydedin.",
  "sync.dormantTitle": "Bağlı yazıcı yok — eşitlenecek bilgi bulunmuyor.",
  "sync.dormantBody": "Yuvalar Bambu Lab entegrasyonu üzerinden okunur. Kurulumdan sonra Filament Ledger'ı yeniden yükleyin; bu düğme gerçek yuva bilgilerini gösterecektir.",
  "sync.noTraysTitle": "Yazıcı şu anda kullanılabilir yuva bilgisi bildirmedi.",
  "sync.noTraysBody": "Sensörüne ulaşılamayan yuva boş sayılmaz; gösterimden çıkarılır. Yazıcıya erişilebildiğinde yeniden deneyin.",
  "sync.doneTitle": "Yazıcıyla eşitlendi",
  "sync.slot": "Yuva [[slot]]",
  "sync.empty": "boş",
  "sync.mounted": "takılı",
  "sync.detected": "algılandı — otomatik takma kapalı olduğu için konumu değiştirilmedi",
  "sync.noTag": "RFID yok — her markadan filament kaydedilebilir",
  "sync.noTagHints": "RFID yok — her markadan filament kaydedilebilir ([[hints]])",
  "sync.ambiguous": "iki makara [[tag]] etiketini paylaşıyor — doğru makarayı elle takın; sistem sizin yerinize seçmez",
  "sync.unknownTag": "bilinmeyen etiket [[tag]]",
  "sync.unknownTagHints": "bilinmeyen etiket [[tag]] · [[hints]]",
  "sync.notInInventory": "envanterde yok",
  "sync.register": "Kaydet…",
  "ams.slot": "Yuva [[slot]]",
  "ams.unit": "AMS [[ams]]",
  "ams.external": "Harici makara",
  "ams.externalLeft": "Harici makara (sol)",
  "ams.externalRight": "Harici makara (sağ)",
  "ams.empty": "Boş",
  "ams.occupied": "Yazıcı burada makara olduğunu bildiriyor",
  "ams.chipless": "Çipsiz bir makara takılı",
  "ams.note": "Konumlar elle atanır. Makarayı taşımak filament tüketmediği için takma işlemi miktar hareketi oluşturmaz.",
  "ams.machineUnnamed": "Yazıcı tanımlanmamış",
  "ams.machineStale": "Bu makaralar şu anda takip edilmeyen bir yazıcıda kayıtlı. Hangi yazıcıda oldukları kaydedilmediğinden sizin yerinize taşınmadılar. Yuvaları bir sonraki açışınızda her makarayı doğru yazıcıya atayın.",
  "history.emptyTitle": "Henüz hareket yok.",
  "history.emptyBody": "Her makaraya giren ve çıkan miktar burada en yeni kayıt önce olacak şekilde görünür: baskılar, düzeltmeler ve atıklar. Bir makara kaydettiğinizde başlangıç bakiyesi ilk satırı oluşturur.",
  "history.emptyFoot": "Buradaki kayıtlar değiştirilmez. Düzeltmeler yeni bir satır olarak eklenir.",
  "history.heading": "Tüm hareketler",
  "history.colWhen": "Zaman",
  "history.colSpool": "Makara",
  "history.colEntry": "Kayıt",
  "history.colAmount": "Miktar",
  "history.colSource": "Kaynak",
  "history.colCorrect": "Düzelt",
  "history.colBalance": "Bakiye",
  "history.foot": "Tüm makaraların en yeni [[count]] kaydı. Bir satırın bakiyeye etkisini görmek için makarayı açın; bakiye yalnızca o makaranın geçmişinden hesaplanır.",
  "history.confirmed": "onaylandı",
  "history.auto": "otomatik",
  "history.deleted": "silindi",
  "history.reassignTitle": "Bu tüketimi başka bir makaraya aktar",
  "history.voidTitle": "Bu kaydı sil ve miktarı iade et",
  "history.groupEntries": "[[count]] kayıt",
  "history.groupUnnamed": "Adsız baskı",
  "history.filterSearch": "Not veya baskı",
  "history.filterSearchPlaceholder": "Ara…",
  "history.filterSearchHelp": "Kayıttaki notu ve ilgili baskının adını arar. Makara adını veya kayıt etiketini aramaz; makaraları renk filtreleriyle daraltabilirsiniz.",
  "history.filterFrom": "Başlangıç",
  "history.filterTo": "Bitiş",
  "history.filterAmount": "Hareket miktarı (g)",
  "history.filterAmountHelp": "Yönünden bağımsız olarak taşınan miktar: 84 g tüketen baskı da 84 g iade eden düzeltme de en az 50 g filtresine uyar.",
  "history.filterAtLeast": "en az",
  "history.filterAtMost": "en fazla",
  "history.filterColour": "Renk",
  "history.filterColourHelp": "Seçtiğiniz renklerdeki makaraların kayıtları. Birden fazla renk seçebilirsiniz; siyah ve griyi birlikte inceleyebilirsiniz.",
  "history.filterColourOne": "[[colour]] filament",
  "history.filterClear": "Filtreleri temizle",
  "history.filterToggle": "Filtreler",
  "history.filterActive": "[[count]] filtreyle daraltıldı",
  "history.noMatchTitle": "Bu filtrelerle eşleşen kayıt yok.",
  "history.noMatchBody": "Tüm kayıtlar hâlâ duruyor; yalnızca bu seçime uyan yok. Tarih aralığını genişletin, bir rengi kaldırın veya tümünü görmek için filtreleri temizleyin.",
  "history.footFiltered": "Filtrelerle eşleşen [[count]] kayıt, en yeni önce. Sınır eşleşen kayıtlara uygulanır; filtreyi genişletmek eski kayıtları da görünür kılabilir.",
  "stats.periodLabel": "Dönem",
  "stats.period30d": "30 gün",
  "stats.period90d": "90 gün",
  "stats.periodall": "Tüm zamanlar",
  "stats.consumed": "Baskıda kullanılan",
  "stats.wasted": "Atık",
  "stats.printsFinished": "Tamamlanan baskılar",
  "stats.reviewsResolved": "Sonuçlanan incelemeler",
  "stats.printTime": "Baskı süresi",
  "stats.printTimeAverage": "Ortalama baskı",
  "stats.printTimeAcross": "Hem başlangıcı hem bitişi kaydedilen [[count]] baskıdan ölçüldü. Tahmin değildir ve dönemdeki her baskıyı kapsamaz.",
  "stats.duration": "[[hours]] sa [[minutes]] dk",
  "stats.durationMinutes": "[[minutes]] dk",
  "stats.byColour": "Renge göre filament",
  "stats.byMaterial": "Malzemeye göre filament",
  "stats.outcomes": "Baskı sonuçları",
  "stats.outcomeFinished": "tamamlandı",
  "stats.outcomeCancelled": "iptal edildi",
  "stats.outcomeFailed": "başarısız",
  "stats.reviewsApproved": "onaylandı",
  "stats.reviewsDismissed": "tüketim kaydedilmedi",
  "stats.reviewsHeading": "Verdiğiniz kararlar",
  "stats.topPrints": "En çok filament kullanan baskılar",
  "stats.colPrint": "Baskı",
  "stats.colWhen": "Başlangıç",
  "stats.colFilament": "Filament",
  "stats.noConsumption": "Bu dönemde hiçbir makaradan filament kullanılmadı.",
  "stats.noTopPrints": "Bu dönemdeki tüketim henüz bir baskıyla ilişkilendirilmemiş.",
  "stats.noOutcomes": "Bu dönemde biten baskı yok.",
  "stats.noReviews": "Bu dönemde sonuçlanan inceleme yok.",
  "stats.emptyTitle": "Henüz hesaplanacak kayıt yok.",
  "stats.emptyBody": "Bu sayfa kayıtlardan hesaplanır: baskıda kullanılan filament, atılan miktar, en hızlı tükenen renk ve malzemeler ile baskı sonuçları. Baskı yaptıkça kendiliğinden dolar; burada elle veri girilmez.",
  "stats.emptyFoot": "Daha önce baskı yaptıysanız ancak bu dönemde yapmadıysanız <b>Tüm zamanlar</b> seçeneğini deneyin. Boş sayfa, bu dönemde kayıt olmadığını belirtir; yükleme hatası anlamına gelmez.",
  "stats.foot": "Tahminlerden değil, kayıtlardan hesaplanır. Silinen makara ve kayıtlar hesaba katılmaz. Atılan filament, baskı tüketimi değil atık sayılır; panelin geri kalanıyla aynı kurallar geçerlidir.",
  "review.emptyTitle": "İncelenecek bir şey yok.",
  "review.emptyBody": "İptal edilen ve başarısız baskılar, tüketilen filamenti onaylamanız için burada görünür. Siz onaylamadan bu baskılar için stoktan düşülmez.",
  "review.emptyFoot": "Yazıcı bağlandıktan sonra bu liste dolmaya başlar. O zamana kadar tüm hareketler elle girdiğiniz kayıtlardır.",
  "review.pending": "[[count]] bekleyen",
  "review.completed": "tamamlandı",
  "review.stoppedAtLayer": "[[total]] katmanın [[layer]]. katmanında durdu",
  "review.stoppedAtLayerPct": "[[total]] katmanın [[layer]]. katmanında durdu (%[[pct]])",
  "review.stoppedAtPct": "%[[pct]] ilerlemede durdu",
  "review.printerError": "Yazıcı hatası",
  "review.rawErrorTitle": "ham print_error [[code]]",
  "review.printerReported": "yazıcının bildirdiği durum: &quot;[[state]]&quot;",
  "review.noDataTitle": "⛔ Tüketim bilgisi yok — yazıcı bu bilgiyi bildirmedi",
  "review.noDataBody": "Bu baskı için stoktan düşülmedi.",
  "review.total": "toplam <b>[[grams]]</b> g",
  "review.weighedSpools": "⚖ Makaraları tarttım:",
  "review.weighedWaste": "⚖ Atığı tarttım:",
  "review.distribute": "Dağıt",
  "review.approve": "✓ Onayla",
  "review.unknownSpool": "Bilinmeyen makara",
  "review.whichSpool": "bu yuvada hangi makara vardı?",
  "review.chooseSpool": "Makara seçin…",
  "review.slotWord": "yuva [[slot]]",
  "review.externalWord": "harici makara",
  "review.externalWord2": "ikinci harici makara",
  "review.blockedHint": "[[slots]] için makara seçilene veya miktarı 0 yapılana kadar onay verilemez.",
  "review.invalidAmounts": "Miktarlar sıfır veya pozitif sayı olmalı.",
  "review.addSpool": "+ Makara ekle",
  "review.loadRest": "Kalanı ata",
  "review.loadRestTitle": "Yuvadaki kalan tüketimin tamamını bu makaraya ata",
  "review.dropChargeTitle": "Bu makarayı yuvadaki tüketimden çıkar",
  "review.remaining": "Atanacak [[grams]] g kaldı",
  "review.overCharged": "Bu yuvanın tüketiminden [[grams]] g fazla atandı",
  "review.remainderHint": "[[slots]] içindeki tüm tüketim makaralara atanana kadar onay verilemez.",
  "detail.back": "← Tüm makaralar",
  "detail.ofOpening": "g / [[opening]] g",
  "detail.deletedNote": "Bu makara çöp kutusunda; hiç kaydedilmemiş gibi değerlendirilir ve hesaplara katılmaz. Geçmişi aşağıda eksiksiz durur; geri yüklemek ikisini de geri getirir.",
  "detail.restoreSpool": "Bu makarayı geri yükle",
  "detail.weigh": "Tart",
  "detail.adjust": "Düzelt",
  "detail.edit": "Düzenle",
  "detail.finish": "Bitti olarak işaretle",
  "detail.finishHelp": "Makara yazıcıdan boş çıkarıldı. Bakiye 0 g yapılır ve fark tartım düzeltmesi olarak kaydedilir; böylece sapma görünür kalır.",
  "detail.remove": "Kaldır…",
  "detail.removeHelp": "Atıldı mı, yanlışlıkla mı kaydedildi? Bunlar farklı durumlar olduğu için sonraki adımda hangisi olduğu sorulur.",
  "detail.heading": "Hareket geçmişi",
  "detail.foot": "Aşağıdan yukarıya okuduğunuzda toplamın nasıl hesaplandığını görürsünüz. Geçmiş değiştirilmez; düzeltme yeni satırdır. Silinen kayıtlar üstü çizili olarak, miktarı iade eden satırın yanında kalır. Bu görünüm toplamı doğrulamak için hiçbir kaydı gizlemez.",
  "fin.emptyTitle": "Henüz biten makara yok.",
  "fin.emptyBody": "Filamenti biten veya atılan makaralar envanterden buraya taşınır; kayıt sisteminden silinmez. Geçmişleri siz sakladığınız sürece okunabilir.",
  "fin.body": "Biten veya atılan makaralar. Geçmişi görmek için makarayı açın. Tartımda filament kaldığı görülürse makara yeniden envantere döner.",
  "trash.emptyTitle": "Çöp kutusu boş.",
  "trash.emptyBody": "Silinen makaralar ve geçmiş kayıtları burada bekler; hepsi geri yüklenebilir. Kayıt sisteminde hiçbir şey tamamen kaybolmaz; silme işlemi de yeni bir kayıt oluşturur.",
  "trash.spoolsHeading": "Makaralar",
  "trash.spoolsBody": "Yanlışlıkla kaydedildikleri için hesaplara katılmazlar. Geri yüklemek makarayı geçmişiyle birlikte depoya döndürür; boşaltılan eski yuvası yeniden atanmaz.",
  "trash.spoolMeta": "[[material]] · [[balance]] g · [[count]] kayıt · silinme: [[when]]",
  "trash.movementsHeading": "Geçmiş kayıtları",
  "trash.movementsBody": "Günlük görünümlerden silindi, kayıt sisteminden değil. Kayıt ve miktarı iade eden satır, makaranın kendi geçmişinde durur.",
  "trash.movementMeta": "[[label]] · <b>[[grams]] g</b> [[direction]] · silinme: [[when]]",
  "trash.movementMetaReason": "[[label]] · <b>[[grams]] g</b> [[direction]] · silinme: [[when]] · [[reason]]",
  "trash.returned": "iade edildi",
  "trash.removed": "çıkarıldı",
  "trash.noRestitution": "silme sırasında miktar iade edilmedi; kayıt hâlâ hesaba katılıyor",
  "trash.spoolDeleted": "önce bu kaydın makarasını geri yükleyin",
  "trash.spoolDiscarded": "bu kaydın makarası atılmış; tüketimin düşüleceği stok yok",
  "dlg.registerTitle": "Makara kaydet",
  "dlg.material": "Malzeme",
  "dlg.materialOther": "OTHER seçiliyse malzeme adı",
  "dlg.materialOtherPlaceholder": "Nylon-X",
  "dlg.colour": "Renk",
  "dlg.openingWeight": "Başlangıç ağırlığı (g)",
  "dlg.coreWeight": "Boş makara ağırlığı (g)",
  "dlg.coreWeightHelp": "Terazi tüm makarayı tartar. Sistem bu ağırlığı toplamdan çıkarır.",
  "dlg.vendor": "Marka",
  "dlg.vendorPlaceholder": "Bambu Lab",
  "dlg.label": "Ad",
  "dlg.labelPlaceholder": "Raf B",
  "dlg.tagFromSlot": "[[slot]] yuvasındaki [[tag]] etiketi eklenecek; sonraki eşitlemede bu makara otomatik olarak takılı kaydedilecek.",
  "dlg.register": "Kaydet",
  "dlg.editTitle": "Makarayı düzenle",
  "dlg.editClearNote": "Marka veya Ad alanını boş bırakmak mevcut değeri korur. Bu formda yalnızca aşağıdaki etiket alanı temizlenebilir.",
  "dlg.tag": "Etiket",
  "dlg.tagDetected": "Yazıcı tarafından eklendi. Fiziksel makarayla eşleşmesi için düzenleme kapalıdır.",
  "dlg.tagPlaceholder": "yok",
  "dlg.tagClear": "Temizle",
  "dlg.tagYours": "Değiştirebilirsiniz; alanı boşaltmak etiketi kaldırır.",
  "dlg.tagNone": "Buradan etiket ekleyebilirsiniz. Elle girilen etiketi daha sonra değiştirebilirsiniz.",
  "dlg.tagDuplicate": "Bu etiketin başka bir makarayla ortak olduğunu onaylıyorum",
  "dlg.correctHeading": "Ağırlığı düzelt",
  "dlg.correctBody": "Bu işlem geçmişe bir hareket ekler; bilgi düzenlemek tek başına bakiyeyi değiştirmez.",
  "dlg.setRemaining": "Kalan filament miktarı (g)",
  "dlg.setRemainingHelp": "Makara hariç net filament. Tartım düzeltmesi olarak kaydedilir.",
  "dlg.addRemove": "Ekle / çıkar (g)",
  "dlg.addRemoveHelp": "Negatif değer çıkarır, pozitif değer ekler. Düzeltme olarak kaydedilir.",
  "dlg.adjustReason": "Düzeltme gerekçesi",
  "dlg.adjustReasonHelp": "Düzeltme için zorunludur. Açıklamasız bir düzeltmeyi hatadan ayırt etmek mümkün değildir.",
  "dlg.correctNothing": "İki alan da boş bırakılırsa geçmişe kayıt eklenmez.",
  "dlg.correctReconcile": "[[from]] g → [[to]] g: [[delta]] g tartım düzeltmesi kaydedilir.",
  "dlg.correctAdjust": "[[delta]] g düzeltme kaydedilir; yeni bakiye [[after]] g olur.",
  "dlg.editCorrectionNote": "Düzenleme ekranından düzeltildi",
  "dlg.weighTitle": "Makarayı tart",
  "dlg.weighBody": "Filament dahil tüm makarayı mutfak terazisine koyun.",
  "dlg.measured": "Tartılan ağırlık (g)",
  "dlg.includesCore": "Makara ağırlığı dahil ([[core]] g)",
  "dlg.weighFoot": "Düzeltme olarak kaydedilir. Mevcut geçmiş kayıtlarınız değiştirilmez.",
  "dlg.finishTitle": "[[name]] bitti olarak işaretlensin mi?",
  "dlg.finishSays": "Kayıtlara göre hâlâ <b>[[grams]] g</b> var. Boş makara kaydetmek <b>[[delta]] g</b> tartım düzeltmesi oluşturur. Bu, son tartımdan beri tahminlerde biriken sapmadır.",
  "dlg.finishFoot": "Miktar atık sayılmaz ve bir baskıya yüklenmez. Siz ölçümü belirtirsiniz; fark sistemin hesaplama sapması olarak görünür kalır. Kaldırana kadar makara envanterinizde 0 g olarak durur.",
  "dlg.finishConfirm": "Boş makara olarak kaydet",
  "dlg.finishNote": "Bitti olarak işaretlendi — makara boş çıkarıldı",
  "dlg.actionsBalance": "[[grams]] g kaldı · [[state]]",
  "dlg.adjustTitle": "Düzelt",
  "dlg.amount": "Miktar (g)",
  "dlg.amountHelp": "Negatif değer çıkarır, pozitif değer ekler.",
  "dlg.adjustFoot": "Gerekçe zorunludur. Açıklamasız bir düzeltmeyi hatadan ayırt etmek mümkün değildir.",
  "dlg.discardTitle": "Atık kaydet",
  "dlg.discardWhat": "Kapsam",
  "dlg.discardPartial": "Makaranın bir kısmı",
  "dlg.discardWhole": "Makaranın tamamı",
  "dlg.discardAmount": "Kısmi atık miktarı (g)",
  "dlg.discardReasonPlaceholder": "dolaşan bölüm",
  "dlg.mountTitle": "[[slot]] yuvasına tak",
  "dlg.mountExternalTitle": "Harici makara olarak tak",
  "dlg.mountNone": "Tüm makaralar takılı. Önce birini çıkarın.",
  "picker.inventory": "Envanterde",
  "picker.spent": "Filament bitmiş",
  "picker.reviewTitle": "Bu yuvada hangi makara vardı?",
  "act.change": "Değiştir",
  "dlg.mountSpool": "Makara",
  "dlg.dismissTitle": "Bu baskı için tüketim kaydedilmesin mi?",
  "dlg.dismissFoot": "Bu işlem silme değildir; karar geçmişe kaydedilir.",
  "dlg.reassignTitle": "Bu tüketimi yeniden ata",
  "dlg.reassignNone": "Tüketimin atanabileceği başka makara yok.",
  "dlg.reassignSays": "<b>[[spool]]</b> makarasına <b>[[grams]] g</b> iade edilir ve seçtiğiniz makaradan <b>[[grams]] g</b> düşülür. İlk kayıt geçmişte yeniden atanmış olarak kalır.",
  "dlg.reassignTo": "Tüketimin atanacağı makara",
  "dlg.reassignAmount": "Aktarılacak miktar",
  "dlg.reassignAmountHelp": "En fazla [[grams]] g, yani tüketimin tamamı. Baskı sırasında makara bitip diğeri devraldıysa daha azını aktarın.",
  "dlg.reassignFoot": "Gerekçe zorunlu değildir. İki makara ve düzeltilen kayıt bağlantısı işlemi açıklar.",
  "dlg.reassign": "Yeniden ata",
  "dlg.voidTitle": "Bu kayıt silinsin mi?",
  "dlg.voidReturns": "<b>[[spool]]</b> makarasına <b>[[grams]] g</b> iade edilir.",
  "dlg.voidRemoves": "<b>[[spool]]</b> makarasından <b>[[grams]] g</b> çıkarılır.",
  "dlg.voidFoot": "Hiçbir şey tamamen silinmez. Kayıt günlük görünümlerden çöp kutusuna taşınır. Silme ve miktar iadesi iki yeni satır olarak kaydedilir.",
  "dlg.voidConfirm": "Kaydı sil",
  "dlg.voidDeletedSpool": "<b>[[spool]]</b> çöp kutusunda olduğu için <b>[[grams]] g</b> iade edilemiyor.",
  "dlg.voidDiscardedSpool": "<b>[[spool]]</b> atılmış olduğu için <b>[[grams]] g</b> iade edilemiyor.",
  "dlg.voidRestoreFirst": "Önce makarayı geri yükle",
  "dlg.voidDiscardRoute": "Atılan bir makarayı geri getirmek için tüm makarayı atık kaydeden hareketi silin. Bakiye ve makara tek işlemle geri gelir.",
  "dlg.voidWhyNothing": "Miktar neden iade edilmiyor?",
  "dlg.voidWhyPlaceholder": "ne olduğunu açıklayın",
  "dlg.voidNoRestitutionFoot": "Burada açıklama zorunludur. Kayıt bakiyeye katılmaya devam eder; yalnızca görünümden kaldırılır. Açıklamasız silmenin nedeni daha sonra anlaşılamaz. Bu işlem sonradan geri yüklenemez.",
  "dlg.voidNoRestitutionConfirm": "Miktarı iade etmeden sil",
  "dlg.restoreTitle": "Bu kayıt geri yüklensin mi?",
  "dlg.restoreDeduct": "<b>[[spool]]</b> makarasından yeniden <b>[[grams]] g</b> düşülsün mü?",
  "dlg.restoreAdd": "<b>[[spool]]</b> makarasına yeniden <b>[[grams]] g</b> eklensin mi?",
  "dlg.restoreFoot": "Kayıt geçmişinize döner ve geri yükleme yeni bir satır olarak kaydedilir. Geçmişte olanlar değiştirilmez.",
  "dlg.intentTitle": "[[name]] makarasını kaldır",
  "dlg.intentAsk": "Makaraya ne oldu?",
  "dlg.intentThrewAway": "Çöpe attım",
  "dlg.intentThrewAwayHelp": "Kalan [[grams]] g istatistiklerde atık sayılır. Makaranın geçmişi korunur.",
  "dlg.intentMistake": "Yanlışlıkla kaydedildi",
  "dlg.intentMistakeHelp": "Hiç kaydedilmemiş gibi değerlendirilir, hiçbir hesaba katılmaz ve Çöp kutusundan geri yüklenebilir.",
  "dlg.staleTitle": "Bu kayıt değişti",
  "dlg.staleBody": "Bu ekran açıkken kayıtlar değişti. Kapatıp yeniden deneyin; hiçbir işlem gönderilmedi.",
  "printer.dormantTitle": "Bağlı yazıcı yok.",
  "printer.dormantBody": "Yazıcı Bambu Lab entegrasyonu üzerinden okunur. Kurulumdan sonra Filament Ledger'ı yeniden yükleyin; bu sekme yazıcının bildirdiği bilgileri gösterecektir.",
  "printer.dormantFoot": "Bu sırada veri uydurulmaz; sürekli yükleniyor göstergesi veya varsayılan dört yuva gösterilmez.",
  "printer.refresh": "⟳ Yenile",
  "printer.heading": "Yazıcı",
  "printer.status": "Durum",
  "printer.job": "İş",
  "printer.progress": "İlerleme",
  "printer.remaining": "Kalan",
  "printer.layer": "Katman",
  "printer.layerOf": "[[current]] / [[total]]",
  "printer.online": "Çevrimiçi",
  "printer.connection": "Bağlantı",
  "printer.activeFeed": "Etkin konum",
  "printer.activeFeedAms": "AMS [[ams]] · [[slot]]",
  "printer.yes": "evet",
  "printer.no": "hayır",
  "printer.errorHeading": "Yazıcı hatası",
  "printer.noError": "Bildirilen hata yok.",
  "printer.hoursHeading": "Burada kaydedilen baskı süresi",
  "printer.hoursObserved": "[[since]] tarihinden beri takip edilen tüm yazıcılardaki [[count]] baskıdan ölçüldü. Yazıcının toplam kullanım sayacı değildir; yazıcılar bu bilgiyi bildirmez. Toplam, yazıcının değil Filament Ledger'ın kurulduğu tarihten başlar.",
  "printer.trackingHeading": "Takip edilen yazıcılar",
  "printer.trackingFollowing": "Takip edilenler: [[serials]].",
  "printer.trackingUnnamed": "Seri numarası okunamayan [[count]] ek yazıcı bulundu ve takibe alınmadı. Aynı adla görünen iki yazıcı aynı yuvaları paylaşmış gibi değerlendirileceğinden, ayırt edilemeyen yazıcı için tahmin yapılmaz. Lütfen bunu bildirin; projede şimdiye kadar görülen her yazıcı seri numarasını bildirmiştir.",
  "printer.traysHeading": "Yuvalar",
  "printer.trayLedger": "kayıt: [[spool]]",
  "printer.trayLedgerEmpty": "kayıt: takılı makara yok",
  "printer.noTrays": "Yazıcı şu anda kullanılabilir yuva bilgisi bildirmedi.",
  "printer.readOnly": "Bu sekmeyi açmak veya Yenile'ye basmak kayıtları değiştirmez. RFID olsun veya olmasın, her markadan yüklü filamenti Kaydet ile içeri aktarabilir; bilgilerini ve ağırlığını onaylayabilirsiniz.",
  "settings.heading": "Varsayılanlar",
  "settings.openingWeight": "Varsayılan başlangıç ağırlığı (g)",
  "settings.openingWeightHelp": "Yeni makaradaki filament miktarı. Bambu makaralar 1000 g'dır.",
  "settings.coreWeight": "Varsayılan boş makara ağırlığı (g)",
  "settings.coreWeightHelp": "Filamentsiz makaranın ağırlığı. Mutfak terazisi tüm makarayı tarttığı için sistem bu değeri çıkarır.",
  "settings.anomalyThreshold": "Bu orandan fazla tartım sapmasını işaretle (%)",
  "settings.anomalyThresholdHelp": "Teraziyle kayıt arasındaki fark bu oranı aşarsa önceki işlemlerde sistematik bir sorun olabilir; kontrol etmek gerekir.",
  "settings.autoMount": "Algılanan filamentleri otomatik ata",
  "settings.autoMountHelp": "Bilinen RFID’li filamentleri ve otomatik kaydedilen RFID’siz filamentleri ilgili yuvalarına ata.",
  "settings.save": "Ayarları kaydet",
  "settings.reloadWarning": "Kaydetmek Filament Ledger'ı yeniden yükler; bir iki saniye sürer.",
  "settings.saved": "Kaydedildi. Filament Ledger bu değerlerle yeniden yükleniyor.",
  "settings.readOnly": "Bu ayarları yalnızca yönetici değiştirebilir. Evdeki herkesin kayıtlarını etkilediği için gizlenmek yerine salt okunur gösterilir.",
  "settings.languageHeading": "Dil",
  "settings.languageAuto": "Otomatik (Home Assistant dilini izle)",
  "settings.languageEn": "English",
  "settings.languageEs": "Español",
  "settings.languageTr": "Türkçe",
  "settings.languageHelp": "Yalnızca bu cihaz için. Panelin dilini değiştirir; kaydedilen verileri değiştirmez. Aynı envanteri farklı kişiler farklı dillerde kullanabilir."
};

const TABLES = { en: EN, es: ES, tr: TR };

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
