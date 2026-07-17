# INPROGRESS #1437 — Aider DeepSeek config: ignore file + task-appropriate reasoning

**Todo:** #1437, pp_ref=PP-HERMES-EA-001

## What happened

Dave routed a pasted DeepSeek/Aider chat transcript (checksum-verified,
`docs/TGW-Plan-Vault/inbox/tigwa/Deepseek-v4-flash-aider-config-tweaks.txt`) via
`TIGWA-NOTE-claude-deepseek-v4-flash-aider-config-tweaks.md` — shelve, don't
implement, discuss first. Discussed. Dave's actual direction: don't adopt the
transcript's fixed main/editor-model split (that's a "particular structure");
the important parts are (a) configuring reasoning to the task, and (b) the
`.aiderignore` guidance.

## What was done

1. Added `.aiderignore` (repo had none) — Python cache dirs, Flutter/Dart
   build artifacts for the real Flutter surface in this repo (`apps/tgw_app/`,
   plus the vendored `flutter/` SDK clone), secrets/env, generated catalog
   files. No Kotlin section — the camera app lives in a separate repo, not
   relevant here. Verified against `.gitignore` for consistency; this is
   defense-in-depth, not a replacement for it.
2. `.aider.conf.yml` — added a `model-settings` block for
   `deepseek/deepseek-v4-flash` defaulting `thinking.type: disabled`
   (non-thinking), since this tier is XS/S mechanical busywork and
   fast/cheap is the point (matches the already-settled 2026-07-15
   single-model-no-architect-split decision in the same file). Documented
   that a per-task `spec-<id>.md` can override `reasoning_effort` upward via
   `--set-model-setting` for a task that genuinely needs deeper reasoning —
   explicitly NOT a hardcoded second model/mode.
3. Verified the API shape against DeepSeek's own docs
   (https://api-docs.deepseek.com/guides/thinking_mode) via WebFetch before
   writing config — thinking toggle + reasoning_effort are real, current
   params (not confabulated by the pasted transcript).

## Remaining

- Run `tgw health` after the config edit (standard post-config-change check).
- No git commit yet — Dave hasn't asked for one.
