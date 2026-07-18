# In progress: todo #1467 aspects-form layout fix

Working in worktree `/opt/TGW/var/worktrees/1467-aspects-form-layout` on branch
`todo/1467-aspects-form-layout`, off `catio-nix-0.0.1-alpha`.

Task: filled-in aspect (Material=Cloisonne on tgw202605051207245) rendered
visually separated from peer aspects on the item-detail aspects form, easy to
overlook. Investigating `loadCatCtx()`/aspects-form rendering in
`src/tgw/http_server.py`, post the 2026-07-16 #1470/#1471/#1472 custom-aspect
checkbox redesign which touched this code heavily. Data itself is correct
(confirmed live on eBay) — this is a rendering/grouping fix only.

Plan: verify live item data first (invariant C11), locate current rendering
logic fresh, identify concrete grouping/ordering/CSS cause, fix, write
manifest to docs/TGW-Plan-Vault/plan/packets/results/1467-RESULT.md.
