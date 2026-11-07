BEGIN;
ALTER TABLE spool ADD COLUMN opened_at TEXT;
ALTER TABLE spool ADD COLUMN colour_known INTEGER NOT NULL DEFAULT 1 CHECK (colour_known IN (0,1));
ALTER TABLE spool ADD COLUMN low_stock_threshold_mg INTEGER CHECK (low_stock_threshold_mg > 0 AND low_stock_threshold_mg <= 10000000);
ALTER TABLE spool ADD COLUMN low_stock_armed INTEGER NOT NULL DEFAULT 0 CHECK (low_stock_armed IN (0,1));
ALTER TABLE spool ADD COLUMN low_stock_baseline_threshold_mg INTEGER NOT NULL DEFAULT 100000 CHECK (low_stock_baseline_threshold_mg > 0);
ALTER TABLE spool ADD COLUMN last_low_stock_occurrence_id TEXT;
ALTER TABLE movement ADD COLUMN opening_basis TEXT;
ALTER TABLE movement ADD COLUMN gross_weight_mg INTEGER;
ALTER TABLE movement ADD COLUMN tare_weight_mg INTEGER;
CREATE TABLE registration_request (
 actor_id TEXT NOT NULL,
 request_id TEXT NOT NULL,
 payload_fingerprint TEXT NOT NULL,
 spool_id TEXT NOT NULL REFERENCES spool(id),
 created_at TEXT NOT NULL,
 PRIMARY KEY(actor_id, request_id)
);
INSERT INTO schema_version (version, applied_at) VALUES (11, datetime('now'));
COMMIT;
