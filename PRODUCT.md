# Product
<!-- impeccable:product-schema 1 -->

## Platform
web

## Users
A Home Assistant owner with a Bambu Lab printer (reference hardware: A1 with AMS Lite), working as a home maker or hobbyist. They keep stock by weighing spools on a kitchen scale and use the panel at a desk or on a phone to check what is left, review deductions the ledger is unsure about, and correct or audit history.

## Product Purpose
Filament Ledger is a Home Assistant custom integration that tracks how much filament is actually left on each spool. The printer cannot report it (the AMS `remain` field is absent or useless on the reference machine), so the amount is accounted for rather than sensed. Success: the user can trust the remaining grams on every spool, avoid prints failing on an empty spool, and avoid rebuying filament already on the shelf.

## Positioning
Accounting instead of sensing. Every gram that leaves a spool is an immutable movement and the balance is their sum (a double-entry ledger per spool). Printer figures are treated as the slicer's plan, not a measurement; anything uncertain goes to a review queue, and only the user's scale is ground truth. It lives as a single sidebar surface inside Home Assistant, with no extra container or database (unlike Spoolman plus SpoolmanSync, which also ignores cancelled prints).

## Operating Context
- Runs inside Home Assistant as a custom integration (HACS custom repository, MIT licence), reading the `ha-bambulab` integration's entities.
- Deducts automatically when a print finishes; cancelled, failed or unseen prints open reviews instead of being silently dropped.
- Spools are identified by the printer's `tray_uuid`, with the RFID `tag_uid` as fallback for third-party or refilled reels.
- Main panel views: Inventory, History, Stats, Review (with count badge), AMS, Printer, Trash, Settings.

## Capabilities and Constraints
- Capabilities: spool registration, automatic per-print consumption, review queue, weigh-and-reconcile, movement void/reassign, trash with restore, low-stock alerts, per-printer AMS view, statistics.
- Terminology to keep: ledger, movement, spool, balance, review, reconcile ("needs weighing"), trash.
- Existing implementation facts (from the repo ADRs and docs, not new user decisions): panel is vanilla JS that repaints by replacing markup (ADR-0006), own visual identity independent of the HA theme, dark by default with light and auto appearance options (ADR-0008, ADR-0011, docs/16-visual-system.md), translations in English, Spanish and Turkish, must stay usable on a phone.
- Non-Bambu printers and multi-printer or print-farm use: not confirmed as targets.

## Brand Commitments
Name: Filament Ledger. Voice in the README and docs is plain, precise and candid about what the system does not know. Brand assets live in `custom_components/filament_ledger/brand/`.

## Evidence on Hand
Real screenshots in `docs/img/` (inventory, history, AMS, stats), a style guide at `custom_components/filament_ledger/www/styleguide.html`, field measurements in `docs/12-field-notes.md`, and specs in `docs/01-vision.md` to `docs/16-visual-system.md`. No testimonials, user counts or benchmarks exist; do not invent them.

## Product Principles
1. Never show a number the system cannot account for; say what is estimated versus weighed.
2. Uncertainty goes to review, never into silence.
3. History is immutable; corrections add movements, they never rewrite the past.
4. One surface inside Home Assistant; every new screen must earn its place.
5. Be plain about limits: state what the ledger does not know.

## Accessibility & Inclusion
Three UI languages (English, Spanish, Turkish) with full string parity. No other product-specific accessibility standard has been established.
