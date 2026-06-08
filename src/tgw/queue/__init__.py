"""TGW queue system."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Canonical worker queue list — single source of truth.
#
# Each entry is the ``<queue>`` of a ``tgw-worker@<queue>.service`` systemd
# template instance (and the ``QUEUE_NAME`` constant inside the matching
# ``tgw.workers.*`` module).  Ordered to mirror the pipeline flow documented
# in CLAUDE.md so ``tgw restart-workers`` / ``tgwlogs`` list them sensibly.
#
# Consumers: ``tgw restart-workers`` (api.py) and the ``tgwlogs`` MC extfs VFS.
# Keep in sync with [project.scripts] in pyproject.toml when adding a worker.
# ---------------------------------------------------------------------------

WORKER_QUEUES: tuple[str, ...] = (
    'token_refresh',
    'pm_intake',
    'bundle_intake',
    'multi_intake',
    'ai_identify',
    'catalog_rebuild',
    'thumbnail_gen',
    'ebay_draft',
    'ebay_upload',
    'ebay_price',
    'ebay_price_reducer',
    'ebay_stage',
    'ebay_publish',
    'ebay_sync',
    'ebay_legacy_sync',
    'ebay_sku_migrate',
    'velocity_stats',
    'echo',
)
