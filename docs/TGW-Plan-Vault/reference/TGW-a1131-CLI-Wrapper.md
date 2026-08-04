# a1131 `tgw` CLI wrapper — reference

**Read when:** working on a1131 (Tigwa's office / thermal-relief host) and
you need CLI access to the real `tgw` inventory tool, or you're
investigating/extending PP-PORTABLE-CATALOG-001 ("get to tgw without
futzing around" — Dave's own framing for why this exists).

## What it is

A thin transparent wrapper that lets `tigwa` (and anyone running as her on
a1131) type `tgw <command> ...` from a1131's fish shell and get the exact
same output as running the real `tgw` CLI on `tgw-prod` directly — because
that's literally what happens under the hood, over SSH, argv preserved
byte-for-byte. **`tgw` is NOT installed on a1131** — there is no local
Python/tgw-api stack there, only this wrapper. The wrapper is the only
"tgw" that exists on that host.

## Who built it, when, why

Built by Tigwa, at Dave's direction, dated **2026-07-15/16** (`stat`
timestamps, confirmed before this doc existed). Dave's own framing,
recorded in the PP-PORTABLE-CATALOG-001 plan section: he "already had
Tigwa build a wrapper to get to tgw without futzing around" — i.e. this
predates and is independent of the Flutter portable-catalog app; it's the
pragmatic CLI-only stopgap for reaching the authoritative `tgw` tool from
a1131 while the Flutter app's own connectivity was unverified.

It sat **entirely undocumented in the Plan Vault** until it was
rediscovered live during todo #1492's Flutter-launch verification session
(2026-07-17/18) — the master plan had explicitly said its location was
"not yet located." This doc (todo #1526) is the fix: give it a permanent,
discoverable home instead of leaving it findable only inside a one-off
result manifest (`docs/TGW-Plan-Vault/plan/packets/results/1492-RESULT.md`,
§2, "The wrapper Tigwa built to reach tgw without the app").

## Exact file paths

Both live in `tigwa`'s home directory on **a1131** (192.168.60.101):

- `~tigwa/.local/bin/tgw-prod` — the actual wrapper, a Python script,
  executable.
- `~tigwa/.config/fish/functions/tgw.fish` — a one-line fish function
  named `tgw` that just calls `~/.local/bin/tgw-prod $argv`, so typing
  `tgw ...` at a1131's fish prompt (as `tigwa`) transparently invokes the
  wrapper. Top-of-file comment (per the #1492 finding): "TGW CLI wrapper:
  the authoritative tgw command runs on tgw-prod."

Both files are owned by `tigwa` and **not world-readable** — reading their
raw source requires being `tigwa` or root on a1131; this doc relies on the
#1492 session's live-verified description of their behavior (below) plus
this session's independent re-execution of `tgw-prod --help`, not a raw
`cat` of the source (a raw read of another persona's private files was
correctly declined by this session's own permission classifier as crossing
the Claude/Tigwa office boundary — see `project-claude-tigwa-role-boundary`
memory. If the exact source text is ever needed, ask Dave or have Tigwa
read/paste it herself).

## What it does

1. `~/.local/bin/tgw-prod` takes `sys.argv[1:]` (whatever was typed after
   `tgw-prod`), JSON/base64-encodes it to preserve exact argv (handles
   spaces/quoting correctly), and runs:
   ```
   ssh -o BatchMode=yes -o ConnectTimeout=10 db@192.168.60.100 <encoded remote command>
   ```
   The remote command decodes the argv on tgw-prod's side and execs
   `fish -c "tgw $argv"` there, using tgw-prod's own Python/fish/tgw
   install — i.e. the **real, authoritative** `tgw` CLI, not a local copy.
2. stdin/stdout/stderr are streamed through the SSH pipe, so the wrapper
   behaves like a normal local command — piping, redirection, and exit
   codes all work as expected.
3. `~/.config/fish/functions/tgw.fish` is purely a convenience shim so you
   don't have to type the full `~/.local/bin/tgw-prod` path — `tgw ...` at
   the fish prompt is enough.

## How it's invoked

From a1131, as `tigwa` (or via `sudo -u tigwa` from another account with
that authorization):

```
tgw <command> [args...]              # via the fish function, tigwa's own shell
~/.local/bin/tgw-prod <command> ...  # direct, works from any shell
```

Example (live-verified this session, 2026-07-18):
```
sudo -u tigwa /home/tigwa/.local/bin/tgw-prod --help
```
returned the full `tgw` CLI usage/subcommand listing — read/search
commands (`get`, `list`, `search`, `resolve`, `quality`, `hint-trail`,
`audit-trail`, `reprice-suggest`, `staged`, `velocity-report`, `seo-audit`,
`locate`) and write/update commands (`update`, `update-where`,
`update-title`, `update-location`, `update-verified`, `update-status`,
`set-shipping`, `bulk`, `price-freeship`, `hint`, `data-scrub`, ...) —
identical to `tgw --help` run natively on tgw-prod. Previously
(#1492, 2026-07-17) `tgw-prod list --limit 2` was also live-verified to
round-trip real ItemData JSON from tgw-prod over this path.

## What it depends on

- **SSH key trust, one direction only:** `tigwa`@a1131 → `db`@tgw-prod
  (192.168.60.100), `BatchMode=yes` (no interactive password fallback — if
  the key trust ever breaks, the wrapper fails closed with a connection
  error, not a hang). This is a *different* key/trust relationship than
  Tigwa's memory-sync SSH key (see `project-tigwa-ssh-memory-sync` memory)
  — don't conflate the two when auditing a1131→tgw-prod SSH access.
- tgw-prod's `fish` shell being present and `tgw` being on `PATH` there
  (or resolvable via `fish -c`) for the user `db`.
- The real `tgw` CLI on tgw-prod itself — this wrapper adds no logic of
  its own beyond argv-safe SSH relay; every subcommand, permission, and
  side effect is exactly what running `tgw` natively on tgw-prod as `db`
  would do. It does **not** go around the `tgw-api` fence or any
  invariant — it's a transport shim, not a new code path into ItemData.

## Relationship to PP-PORTABLE-CATALOG-001

This wrapper gives Dave/Tigwa full read/write CLI access to `tgw` from
a1131 today, with zero Flutter/mobile dependency — but it's terminal/CLI
only (no browse-by-photo grid, no touch UI), so it's a narrower,
already-working capability alongside the Flutter app's still-unreliable
visual browse/review screens, not a substitute for them. See
`pp/PP-PORTABLE-CATALOG-001.md` for the full phased plan; see
`docs/TGW-Plan-Vault/plan/packets/results/1492-RESULT.md` for the original
discovery and live-verification evidence.
