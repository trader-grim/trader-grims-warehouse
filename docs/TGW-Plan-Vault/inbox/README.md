# Plan intake inbox

## Topology (2026-07-15, PP-HERMES-EA-001 #1435/#1436)

```text
inbox/            shared/unassigned intake staging (this directory, direct children)
inbox/dave/       Dave-originated intake
inbox/tigwa/      Tigwa-originated intake
inbox/claude/     Claude's own correspondence — NOT an intake source, never scanned
inbox/queued/     worker staging — NOT a source
inbox/archive/    retained transient history — NOT a source
inbox/review/     review holds (flag_for_review) — NOT a source
```

Drop a Markdown note (a half-formed plan, a meeting brain-dump, a spec fragment)
into the root, `dave/`, or `tigwa/`. `tgw admin-file` (run manually — the
`pm_intake` worker itself is stopped, see CLAUDE.md) discovers eligible notes
from those three locations only, ages them past the submission-delay gate
(`pm_intake_delay_hours`, default 4h; `--now` bypasses it), and enqueues a job:
an LLM reads it, decides what changed, and files it into TGW-Master-Plan.md or
`reference/`/`perplexity/`/`dev-workflow/research/`.

`inbox/claude/` is Claude's own inbox (handoffs/requests addressed to Claude) —
it is never treated as general PM-intake material. Same-named notes from
different owners never collide: queued/archived paths are owner-qualified
(`queued/dave/x.md` vs `queued/tigwa/x.md`), and job payloads carry
owner/source_path/sha256 for provenance.

Use `tgw admin-file --dry-run` to preview what would be discovered/enqueued
without moving or enqueuing anything.
