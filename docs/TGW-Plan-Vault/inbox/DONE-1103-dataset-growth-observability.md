# DONE — todo #1103: Data Charter dataset-growth lines in ops-digest

`ops_digest._dataset_growth()` adds two cheap (stat/listdir only, no
re-parsing) metrics: today's `incoming/ebay/<date>.jsonl.gz` byte size
(with delta vs the last digest run, same-UTC-day gated so a fresh day's
smaller file never falsely reads as a stall), and ItemArchive coverage
(`archived_items/total_items`, E5/#1104's archive_root).

**The Data Charter alarm (Dave's stated purpose):** if any `ebay_*` quota
pool shows spend since the last digest look but the capture file didn't
grow a single byte, the digest flags `RED DATASET GROWTH` — "something is
discarding raw responses again" (Prime Directive 1 / invariant E7).

Live-verified: `tgw ops-digest` shows real numbers matching tonight's work
exactly — 63,519,608 bytes captured today, 20,420/55,419 items archived
(36%). 7 new unit tests (`tests/test_ops_digest_dataset_growth.py`). Full
suite: 1786 pass / 1 skipped / 0 fail / 0 errors (was 1779).
