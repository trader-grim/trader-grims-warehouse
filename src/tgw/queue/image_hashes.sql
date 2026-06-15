-- image_hashes — perceptual-hash cache for vision API deduplication
-- Part of PP-PLANDB-001 (pHash dedup for alt_text + ai_identify workers)
--
-- PRIMARY KEY (phash, task): same image may have different cached results
-- per task (alt_text shape vs ai_identify shape).

CREATE TABLE IF NOT EXISTS image_hashes (
    phash       TEXT        NOT NULL,
    task        TEXT        NOT NULL,
    sku         TEXT        NOT NULL,
    result_json JSONB       NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (phash, task)
);

CREATE INDEX IF NOT EXISTS idx_image_hashes_sku ON image_hashes(sku);
