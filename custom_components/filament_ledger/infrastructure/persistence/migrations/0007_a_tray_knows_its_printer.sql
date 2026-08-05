-- 0007 — a tray knows its printer. See docs/02-domain-model.md §2.3, docs/05-ha-integration.md
-- §5.8 and docs/08-data-model.md §8.2.
--
-- A tray was identified by a bare index, and two printers both have a tray 1. Every surface
-- that named a slot now names a three-part reference — printer, AMS unit, tray — so the
-- model can hold what the hardware actually is. **Nothing here changes behaviour:** the
-- gateway still resolves one printer, the ledger still follows one, and a second machine is
-- representable rather than supported.
--
-- **Where the serial comes from, honestly.** Nowhere: a migration runs inside
-- `Database.migrate()` with a bare SQLite connection, no Home Assistant and no gateway, so
-- there is nothing here to ask. Every row therefore takes `UNIDENTIFIED` — the sentinel
-- `identifiers.py` defines — and the composition root reconciles it once discovery has a
-- real serial to offer (`printer_adoption.py`). That is a placeholder for a *name*, never
-- for a *fact*: this ledger has always talked to exactly one printer, so every row here
-- belongs to that one printer whatever it turns out to be called. A single-printer history
-- cannot be ambiguous, which is the whole argument that this migration is lossless.
--
-- **A ledger whose printer is never seen again keeps the sentinel for ever, and nothing
-- breaks.** Uniqueness holds under it exactly as under a real serial — all the rows share
-- one printer, which is true — the inventory reads the same, and the panel goes on mounting
-- into the same tray space. The only thing that never happens is the rows learning a name.
--
-- Same shape as 0003, 0005 and 0006: one self-contained transaction whose last statement
-- records the version. Either all of it lands or none of it does.

BEGIN;

-- §2.3 — the two halves of the reference the schema did not carry. Nullable, because a
-- spool in storage or on the direct feed is in no tray: they are set exactly when
-- `location_slot` is, which is the pairing 0001's CHECK already states for the slot.
-- SQLite's ADD COLUMN cannot carry a cross-column CHECK, so the pairing is enforced in the
-- domain — the same division 0003 made for `tag_source`.
ALTER TABLE spool ADD COLUMN location_printer TEXT;
ALTER TABLE spool ADD COLUMN location_ams     INTEGER;

-- Every mounted row, at once. AMS 1 because that is the only AMS this ledger has ever
-- followed and the only ordinal the printer has ever reported for it (docs/12: an A1 with
-- AMS Lite, reported as `AMS 1`) — the gateway drops any other ordinal with a warning and
-- did so before this migration existed.
UPDATE spool
   SET location_printer = 'UNIDENTIFIED',
       location_ams     = 1
 WHERE location_kind = 'AMS_SLOT';

-- The invariant, restated over the whole reference: one spool per *tray*, not one spool per
-- tray number. 0003's exclusions survive verbatim — a discarded or deleted spool occupies
-- nothing, and dropping either clause here would resurrect the ghost 0003 removed.
--
-- No existing row can collide. The rows all take one printer and one AMS index, so the
-- triple is unique exactly where the slot alone already was, and the index 0003 created was
-- already enforcing that. Recreating an index is not destructive; the data is untouched.
DROP INDEX idx_spool_slot;
CREATE UNIQUE INDEX idx_spool_slot
    ON spool(location_printer, location_ams, location_slot)
    WHERE location_kind = 'AMS_SLOT' AND discarded_at IS NULL AND deleted_at IS NULL;

-- The four JSON documents that key a figure by tray. Each becomes a list of entries naming
-- the tray in full — `tray_json.py` states why a list and not a composite key — and every
-- entry takes the same `(UNIDENTIFIED, 1, its slot)` the spool rows took, for the same
-- reason and with the same guarantee.
--
-- `json_each` over an empty document yields no rows and `json_group_array` then returns
-- `[]`, so an empty report stays an empty report. A NULL column stays NULL: a figure that
-- never materialised is not a figure of zero, and the whole of UC-04 turns on the
-- difference.

UPDATE print_job
   SET reported_usage = (
           SELECT json_group_array(
                      json_object(
                          'printer', 'UNIDENTIFIED',
                          'ams', 1,
                          'slot', CAST(entry.key AS INTEGER),
                          'mg', entry.value
                      )
                  )
             FROM json_each(print_job.reported_usage) AS entry
       )
 WHERE reported_usage IS NOT NULL;

UPDATE pending_review
   SET estimated_usage = (
           SELECT json_group_array(
                      json_object(
                          'printer', 'UNIDENTIFIED',
                          'ams', 1,
                          'slot', CAST(entry.key AS INTEGER),
                          'mg', entry.value
                      )
                  )
             FROM json_each(pending_review.estimated_usage) AS entry
       ),
       confirmed_usage = CASE
           WHEN confirmed_usage IS NULL THEN NULL
           ELSE (
               SELECT json_group_array(
                          json_object(
                              'printer', 'UNIDENTIFIED',
                              'ams', 1,
                              'slot', CAST(entry.key AS INTEGER),
                              'mg', entry.value
                          )
                      )
                 FROM json_each(pending_review.confirmed_usage) AS entry
           )
       END,
       -- Already a list since 0004; it gains the two columns the others gain and keeps its
       -- `spool_id`, so a frozen attribution survives entry for entry.
       slot_resolution = (
           SELECT json_group_array(
                      json_object(
                          'printer', 'UNIDENTIFIED',
                          'ams', 1,
                          'slot', json_extract(entry.value, '$.slot'),
                          'spool_id', json_extract(entry.value, '$.spool_id'),
                          'mg', json_extract(entry.value, '$.mg')
                      )
                  )
             FROM json_each(pending_review.slot_resolution) AS entry
       );

INSERT INTO schema_version (version, applied_at) VALUES (7, datetime('now'));

COMMIT;
