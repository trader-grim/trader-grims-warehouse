-- sku_history — audit trail for SKU renames
-- Part of PP-ADD-005 SKU normalization

CREATE TABLE IF NOT EXISTS sku_history (
    id            BIGSERIAL PRIMARY KEY,
    sku_old       TEXT NOT NULL,
    sku_new       TEXT NOT NULL,
    changed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    change_reason TEXT NOT NULL,   -- e.g. 'normalize_class_a', 'normalize_class_b', etc.
    changed_by    TEXT NOT NULL,   -- e.g. 'sku_migrate_script'
    had_ebay_listing BOOLEAN NOT NULL DEFAULT FALSE,
    notes         TEXT
);

CREATE INDEX IF NOT EXISTS idx_sku_history_old ON sku_history(sku_old);
CREATE INDEX IF NOT EXISTS idx_sku_history_new ON sku_history(sku_new);
CREATE INDEX IF NOT EXISTS idx_sku_history_changed_at ON sku_history(changed_at);
