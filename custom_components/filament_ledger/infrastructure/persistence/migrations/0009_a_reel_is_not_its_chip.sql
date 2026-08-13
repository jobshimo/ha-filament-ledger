-- 0009 — a reel is not its chip. See docs/02-domain-model.md §2.3, docs/08-data-model.md
-- §8.1 and docs/12-field-notes.md.
--
-- Until now a reel was recognised by `tag_uid`. That was wrong, and measurably so: a Bambu
-- spool carries a tag readable from either side of its hub, the AMS has two reader boards
-- between four trays, and slots 1 and 3 read the opposite side from slots 2 and 4. The same
-- reel therefore reports one chip UID in an odd tray and a different one in an even tray, so
-- moving a reel across that boundary made the ledger register a reel it already owned. The
-- reference machine did it three times out of three, and the printer had been reporting the
-- right answer all along in a field nothing read: `tray_uuid`, which is what Bambu Studio
-- shows as the spool's SN and which held still across eight days and three trays.
--
-- **Nothing here is backfilled with an identity that was never recorded**, which is
-- docs/08 §8.4's rule. `reel_uid` is added nullable and left null on every existing row: the
-- ledger never stored `tray_uuid`, and writing one in now would be this migration inventing
-- the very answer the column exists to stop being guessed at. Old rows learn their reel the
-- first time the printer reads them again — see `DetectSpool`, which adopts the reel onto the
-- row a chip already resolves to.
--
-- **Nothing here merges anything.** An install that has been running on the old rule may
-- already hold two rows for one physical reel, each with its own opening balance and half of
-- the history. Those are the rows this release exists to reunite, and reuniting them is a
-- decision with a scale in it — which row survives, which opening balance is the real one.
-- A migration that guessed would be a migration that silently rewrote a stranger's inventory
-- on an update they clicked through in HACS. So the schema is made able to *represent* the
-- truth here, the pairs surface as merge candidates once their reels are known, and the
-- merge itself waits for the user to confirm it in the panel.

BEGIN;

-- §8.1 — which physical reel this row is. Nullable forever, not merely for the backfill:
-- third-party reels, refills and unreadable hubs have no `tray_uuid` to carry, and those
-- rows go on resolving by chip exactly as they do today.
ALTER TABLE spool ADD COLUMN reel_uid TEXT;

-- §8.1 — why a spool is in the Trash, when it was not a person who put it there.
--
-- Retiring a row the *user* never asked to retire is a heavy thing to do on an update they
-- clicked through, and it is only defensible if they can see it happened, see why, and undo
-- it. `deleted_at` already gives them the Trash and the restore; this gives them the
-- sentence. Null for every row a person deleted, which is every row that exists today —
-- the Trash goes on saying nothing extra about those, because there is nothing extra to say.
ALTER TABLE spool ADD COLUMN deleted_reason TEXT;

-- Lookup only — deliberately **not** UNIQUE, and the restraint is load-bearing twice over.
--
-- Once for the update: a ledger written under the old rule can legitimately hold two rows
-- that will turn out to name one reel, and a unique index would abort this transaction on
-- somebody else's data and leave them unable to start. Once for the runtime: those two rows
-- learn their reel from live tray readings, and under a unique index the second one to learn
-- would raise inside a detection the user cannot see, cannot retry, and did not ask for.
-- Both failures replace a visible, fixable duplicate with an invisible, unfixable one.
--
-- What stops drift is upstream of the schema: `DetectSpool` resolves by reel before it
-- considers registering, so nothing new is ever born for a reel already known.
CREATE INDEX idx_spool_reel ON spool(reel_uid) WHERE reel_uid IS NOT NULL;

-- §8.1 — every chip UID known to belong to a reel. A reel has two sides and therefore up to
-- two of these, and the pair is discovered one tray at a time rather than announced, so this
-- has to be a set that grows and not a second column that would fill up and then be wrong.
--
-- `PRIMARY KEY (spool_id, tag_uid)` makes re-observing a known chip a no-op under
-- `INSERT OR IGNORE`, which is what the detection path wants: it sees the same chip on every
-- republish and must not care.
--
-- `tag_uid` alone is **not** unique, for the update-safety reason above: duplicates by tag
-- were legal under the old rule and some ledgers will contain them. Two rows claiming one
-- chip stays representable, and the resolution path answers it the way it always has — by
-- naming the candidates and asking, never by picking one.
--
-- ON DELETE CASCADE because a chip belongs to a reel and outlives nothing. Deletion in this
-- ledger is `deleted_at`, not `DELETE`, so this fires only for a genuine row removal.
CREATE TABLE spool_tag (
    spool_id     TEXT NOT NULL REFERENCES spool(id) ON DELETE CASCADE,
    tag_uid      TEXT NOT NULL,
    -- Audit, not logic: which side a reel was first met by is the sort of thing that is
    -- obvious for a week and unrecoverable afterwards.
    first_seen_at TEXT NOT NULL,

    PRIMARY KEY (spool_id, tag_uid)
);

CREATE INDEX idx_spool_tag_uid ON spool_tag(tag_uid);

-- Seed the set from what each row already knows. `spool.tag_uid` keeps its meaning — the tag
-- the spool was *registered* with, which is what the panel shows and what `tag_source`
-- qualifies — and this table becomes the index every lookup goes through, so the two never
-- disagree about whether a chip belongs to a reel.
--
-- `registered_at` stands in for a first sighting nobody recorded. It is the earliest moment
-- the row can honestly claim to have known the chip, and it is not a guess: the tag was
-- present at registration or the column would be null.
--
-- The sentinel is excluded rather than assumed absent. Migration 0002 scrubbed sixteen zeros
-- out of this column and `TagUid` has refused it since, but a seed that trusted that would be
-- a seed that indexes an absence as an identity on any row those two ever missed.
INSERT INTO spool_tag (spool_id, tag_uid, first_seen_at)
SELECT id, tag_uid, registered_at
  FROM spool
 WHERE tag_uid IS NOT NULL
   AND trim(tag_uid) <> ''
   AND tag_uid <> '0000000000000000';

INSERT INTO schema_version (version, applied_at) VALUES (9, datetime('now'));

COMMIT;
