-- 0006 — the printer's own start and end, beside the ledger's rather than over them.
-- See docs/05-ha-integration.md §5.8 and docs/06-ui-spec.md §6.7.
--
-- `started_at` and `ended_at` are stamped when Home Assistant processes the lifecycle
-- events, so a print's recorded duration has always included however long the bus, a
-- restart or a reload took. The machine reports both moments itself, and those two figures
-- are the better measurement.
--
-- They arrive as *new columns* rather than as better values for the old ones, and that is
-- the whole decision. UC-04 derives every consumption movement's `occurred_at` from
-- `ended_at`, and the ledger orders itself by `occurred_at` — running balances, the
-- newest-first history, and the anchor window confidence is derived from. A printer's
-- clock running a few minutes behind would sort its print before a reconciliation that
-- really happened first, which drops the print out of that window and reports a confidence
-- the spool has not earned. Nothing reads these two columns except the duration
-- subtraction, so no ordering in the ledger can see them.
--
-- Same shape as 0003 and 0005: one self-contained transaction whose last statement records
-- the version, and additive throughout. Existing rows take NULL, which is the honest answer
-- — those prints ran before anything asked the machine what time it was — and the duration
-- falls back to the ledger's pair exactly as it always has.

BEGIN;

ALTER TABLE print_job ADD COLUMN printer_started_at TEXT;
ALTER TABLE print_job ADD COLUMN printer_ended_at   TEXT;

INSERT INTO schema_version (version, applied_at) VALUES (6, datetime('now'));

COMMIT;
