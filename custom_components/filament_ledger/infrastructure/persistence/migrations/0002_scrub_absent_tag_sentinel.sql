-- 0002 — scrub the absent-tag sentinel out of legacy spool rows.
--
-- The printer reports sixteen zeros for a tray whose spool has no readable tag: absence,
-- never a serial (docs/12-field-notes.md). Before `TagUid` refused the sentinel it was a
-- legal, savable value, and one saved row is enough to take the whole integration down —
-- hydration raises, every query fails, and the entry never loads. Absence is what the
-- sentinel means, so absence is what these rows get to say: NULL.
--
-- Same shape as 0001: one self-contained transaction whose last statement records the
-- version, so either the scrub and the version row land together or neither does.

BEGIN;

UPDATE spool SET tag_uid = NULL WHERE tag_uid = '0000000000000000';

INSERT INTO schema_version (version, applied_at) VALUES (2, datetime('now'));

COMMIT;
