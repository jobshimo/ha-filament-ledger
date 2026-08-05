-- 0004 — a tray's consumption may be attributed to more than one spool. See
-- docs/02-domain-model.md §2.3 and docs/08-data-model.md §8.2.
--
-- The printer reports one figure per tray and can report nothing else, so `estimated_usage`
-- is already the right shape and does not move. What was wrong is `slot_resolution`: a map
-- of one spool per tray, which cannot say that a spool emptied mid-print and the rest of
-- the tray's grams came off the spool that replaced it. It becomes a list of charges,
-- `[{slot, spool_id, mg}]`, and the same tray may appear in it more than once.
--
-- The rewrite is mechanical and there is nothing to interpret. Every existing entry becomes
-- exactly one charge for its tray, carrying the amount that tray was charged: the confirmed
-- figure where a decision recorded one, the estimate otherwise. A null entry becomes no
-- charge at all, which is already what an unresolved tray means. Both directions round-trip,
-- so no review says anything different afterwards than it said before.
--
-- Same shape as 0001-0003: one self-contained transaction whose last statement records the
-- version — either all of it lands, or none of it does. The column keeps its name and its
-- type; renaming it would rewrite a table for a word, and forward-only migrations pay for
-- that at every install (§8.4).

BEGIN;

UPDATE pending_review
SET slot_resolution = (
    SELECT json_group_array(
        json_object(
            'slot',     CAST(entry.key AS INTEGER),
            'spool_id', entry.value,
            -- COALESCE in decision order. A resolved review's charge has to carry what was
            -- actually deducted, not what was once proposed, or `confirmed_charges` would
            -- report the estimate for every approval already in the ledger. The final zero
            -- is unreachable while the two maps share a key set by construction, and it is
            -- here so a row that somehow lost an estimate migrates to an honest nothing
            -- rather than to SQL NULL, which no charge can hold.
            'mg',       COALESCE(
                            json_extract(pending_review.confirmed_usage,
                                         '$."' || entry.key || '"'),
                            json_extract(pending_review.estimated_usage,
                                         '$."' || entry.key || '"'),
                            0
                        )
        )
    )
    FROM json_each(pending_review.slot_resolution) AS entry
    WHERE entry.value IS NOT NULL
);

INSERT INTO schema_version (version, applied_at) VALUES (4, datetime('now'));

COMMIT;
