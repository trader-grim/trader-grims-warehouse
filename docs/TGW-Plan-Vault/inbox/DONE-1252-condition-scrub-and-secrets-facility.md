# INPROGRESS #1252 — code-review follow-ups

Fixing findings #1 and #4 from the code-review of #1178/#1209 work:
1. conditions.py best_condition() has the same MIN-over-list upgrade-risk
   bug as best_condition_for_enum() (fixed in #1178) — item_rank = min(...)
   over preferred_ids can sit below the primary entry's rank when a label's
   fallback list isn't rank-ascending (e.g. 'refurbished', 'nos').
4. data_scrub_legacy_ebay_fields.py still has no guard preventing deletion
   of 'eBay category 1 number'/'eBay category 1 name' on an item that hasn't
   been promoted to the canonical ebay_category_id field yet — #1209 only
   fixed the backfill script's skip logic, not the scrub script's delete gate.

Also disabling tgw-worker@pm_intake.service per Dave's direction (different
approach for pm intake being planned) and documenting the quota.py/llm.py
provider changes (direct Gemini/Anthropic/DeepSeek keys added, OpenRouter
demoted to backup — intentional, just undocumented) plus current worker
status in CLAUDE.md / reference docs.

## Resolution (expanded beyond original scope per Dave's direction mid-session)

### #1 — conditions.py best_condition() MIN->MAX
Same fix as #1178's best_condition_for_enum(): item_rank now MAX across
preferred_ids, not MIN (several _ITEM_CONDITION_PREFERRED lists aren't
rank-ascending, e.g. 'refurbished': ['2500' rank6,'3500' rank5,'2000' rank4]).
New tests: tests/test_best_condition.py (5 cases).

### #4 — data_scrub_legacy_ebay_fields.py deletion-site guard
_scan_item() now imports recompile_category_backfill._canonical_category()
and holds back 'eBay category 1 name'/'eBay category 1 number' unless the
item's canonical ebay_category_id is already populated — the real fix for
the ordering hazard (#1209 only fixed the promotion side, not the deletion
side). New held_pending_promotion report bucket, never silent.

### pm_intake disabled
Stopped (systemctl stop) per Dave: "different direction for pm intake."
Still shows systemd-enabled (Nix-managed /etc, read-only) — flake change
needed to make it durable across reboot, not done.

### Single secrets facility (Dave: "why change code just to change models...
single facility" for keys)
New tgw.apis.secrets.get_api_key()/get_secret() — env-var convention
(PROVIDER_API_KEY), sourced from secrets_root/tgw.env by
tgw.config.load_config(). Replaced 9 separate ad-hoc credentials.json
readers: llm.py (deepseek/anthropic/openrouter loaders), google_genai.py,
pricing.py (a 4th independent openrouter loader found mid-refactor), and
5 apis/lookup/*.py modules (discogs, go_upc, pricecharting, upcitemdb,
igdb — igdb needed 2 values, added get_secret() for that). Old JSON files
migrated into tgw.env and moved to secrets_root/_migrated-to-tgw-env-20260709/
(not deleted). hermes.env confirmed intentionally owned by db (Dave).

### Config-driven model routing (Dave: "we should not have hardcoded even
defaults for the models")
Removed llm.py's hardcoded _DEFAULTS fallback dict entirely — get_task_model()
now reads ONLY cfg['models'][task] (from /opt/TGW/config/tgw-models.json,
a separate file from tgw-api-config.json) and raises KeyError if a task
isn't configured. Correction during this work: initially misdiagnosed
OpenRouter-demotion as "not wired up" by checking the wrong config file
(tgw-api-config.json's empty embedded 'models' key) — tgw-models.json was
already fully and correctly configured with the 2026-07-08 decision. No
routing bug existed; only the stale hardcoded _DEFAULTS copy and llm.py's
stale docstring did.

### Follow-up logged
Todo #1253 (planning, not started): extend the secrets facility to
interactive shell use, and design scoped/least-privilege key issuance per
confined worker/agent for Catio — today's single-env-file approach is fine
for one trusted tgw user but not for isolated workers.

## Evidence
- pytest -q: 1889 passed, 2 pre-existing unrelated failures (test_invariants_pricing.py,
  confirmed via git stash before this session started).
- tgw health: quota check shows live llm_google=40/300 spend — facility
  confirmed working against the real running system, not just tests.
- Docs updated: CLAUDE.md (Settled Architecture + Current Phase), invariants.md
  E2, TGW-Config-Reference.md (new Secrets Reference + model routing sections),
  LLM-Providers-Quotas.md (SUPERSEDES note, historical section marked).
