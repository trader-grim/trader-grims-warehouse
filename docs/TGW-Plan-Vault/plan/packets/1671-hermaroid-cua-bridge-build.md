# Packet #1671 — hermaroid CUA bridge: system-side foundation (real build)

**pp_ref:** PP-CATIONIX-001
**Related:** #1665 (original, over-broad ACL design, superseded), #1670
(sizing/fixture — proved the architecture on a throwaway hermaroid Xvfb
session, all torn down afterward)
**Size:** S (no flake changes — confirmed by #1670's sizing pass; a small
number of new files: a start/stop script pair + a systemd user unit or
equivalent, plus a socket ACL)
**Do NOT dispatch until this packet is reviewed and accepted** — Tigwa
explicitly asked for the formal packet before authorizing implementation
(`TIGWA-RESPONSE-hermaroid-formal-build-packet-before-handoff-2026-07-22`).

## Context budget

Read the #1670 fixture agent's full report (task-notification transcript,
already summarized in this session — the sizing verdict and the 5 proof
results) and Tigwa's two replies (`TIGWA-ACK-hermaroid-client-bridge-
ownership-2026-07-22.md`, `TIGWA-RESPONSE-hermaroid-formal-build-packet-
before-handoff-2026-07-22.md`) before starting. Do not re-derive the
architecture from scratch — it's already settled (see Spec below).

## Background

Dave wants a dedicated guided-session/inventory lab (`hermaroid` on
a1131) where he demos his own workflow to Tigwa, with Tigwa's CUA driver
able to observe/act during the guided session — without ever touching
Dave's (`db`) real desktop session, browser, or credentials. Tigwa
reviewed the original design (ACL sharing `db`'s live Xauthority cookie
into `tigwa`'s account) and rejected it as too broad. Her counter-design,
confirmed feasible by #1670's fixture:

- `cua-driver serve` runs as a daemon **inside hermaroid's own session**
  (own DISPLAY/XAUTHORITY/DBus context) — never shares or reads `db`'s
  Xauthority, never shares or reads Tigwa's own account's session state.
- Tigwa/Hermes is a **client of a narrow authenticated local socket
  bridge** to that daemon — not a reader of hermaroid's Xauthority
  cookie directly.
- The bridge exists **only while a guided session is active** — explicit
  start/stop/revoke lifecycle, not a standing always-on service.

#1670 proved this architecture works end-to-end on a throwaway fixture
(Xvfb `:50`, Mousepad, `db` standing in as a placeholder client identity)
and required **zero flake changes** — hermaroid already exists as a plain
user; the display doesn't need SDDM/seat0/autologin, just an on-demand
headless Xvfb session.

**Flagged finding carried forward from #1670, do not repeat:** the
sizing/fixture agent's first attempt included `cua-driver serve
--dangerously-bypass-approvals` unprompted, before even trying default
`standard` mode. That specific command was blocked by the permission
classifier before it ever reached a1131 — it never executed, and every
actual fixture proof (capture, AX-tree, click, keystroke, revocation) ran
under default `standard` permission mode. Tigwa asked that this be
preserved as raw evidence, not normalized away, and that this build
either replace it with an explicit no-bypass verification or state a
clear blocker if `standard` mode turns out to be genuinely insufficient
for something real work needs. **This packet requires `standard`
permission mode throughout — no `--dangerously-*` flag of any kind,
under any justification, without a separate explicit authorization
naming that flag specifically.** If `standard` mode blocks something
this packet actually needs, stop and escalate — do not add the flag
yourself.

## Revision 2 (2026-07-22) — four contract gaps from Tigwa's first review

Tigwa reviewed rev 1 and found the direction sound but blocked dispatch on
four specific gaps (`TIGWA-REVIEW-packet-1671-hermaroid-bridge-contract-
gaps-2026-07-22.md`). The Spec below is revised to require all four
explicitly. Do not treat any of the four as optional or as something the
executor can decide ad hoc — each is a named acceptance requirement.

## Spec

Build the persistent (but on-demand, not always-on) version of what #1670
proved:

1. **Session lifecycle scripts**, run as `hermaroid` (via `sudo -u
   hermaroid` or an equivalent narrow mechanism):
   - `start`: launches a headless Xvfb session (same shape as #1670's
     `:50` + a minimal WM — `openbox` or equivalent) plus `cua-driver
     serve` bound to a socket, in **default `standard` permission mode**.
     Use hermaroid's *actual discovered* runtime UID/path at execution
     time (`id -u hermaroid`, `/run/user/<that-uid>/...`) — do not
     hardcode `1002` anywhere in the scripts; the packet's own prior
     draft did and that's exactly the kind of assumption that breaks if
     the account is ever recreated.
   - `stop`: tears down the daemon, the WM, and the Xvfb server; removes
     the socket and the Xauthority cookie (see item 2). Idempotent — safe
     to call when nothing is running.
   - `crash-cleanup`: if the daemon/Xvfb died without a clean `stop`
     (process not found but socket/Xauthority file present), clean up
     leftover state safely before a fresh `start`. State explicitly what
     "crashed" detection looks like.
   - **Trigger identity/mechanism — named explicitly, not left ambiguous
     (gap 4):** state exactly who/what is allowed to invoke `start`/
     `stop` and how (e.g. "Dave or Claude runs `sudo -u hermaroid
     <script>` interactively for now; no automatic/unattended trigger,
     no new sudo rule granted to `hermaroid` or `tigwa` to self-invoke
     this"). If a narrower mechanism is built (a wrapper script with a
     bounded sudoers entry), document exactly what it permits and
     nothing else.
2. **X authentication — explicit lifecycle, not implied (gap 2):**
   - Generate a fresh, hermaroid-owned Xauthority cookie on `start`
     (`xauth generate` / `mcookie` into a hermaroid-owned file, mode
     `600`), pass it to Xvfb via `-auth <path>` — **never** `-ac`
     (unauthenticated local access is explicitly prohibited).
   - The daemon and WM must run with `XAUTHORITY` pointed at that file,
     not a shared/ambient one.
   - `stop`/`crash-cleanup` must delete the Xauthority file. Verification
     (below) must show the file is gone after `stop` and that a
     connection attempt without the (now-deleted) cookie fails.
3. **Socket reachability — full traversal chain named (gap 1):**
   - Do not assume an ACL on the socket's immediate parent directory is
     sufficient — name **every** parent directory in the path from `/`
     to the socket that needs `x` (traversal) for the placeholder client
     identity, and grant `setfacl -m u:<identity>:x` on each one
     explicitly, or relocate the socket to a purpose-created local-only
     path (e.g. under a directory this packet creates and fully
     controls, like `/run/hermaroid-cua/`) where the complete
     ownership/traversal chain is defined by this packet rather than
     inherited from XDG runtime-dir defaults. Prefer the purpose-created
     path — it's simpler to reason about and matches "narrowest reversible
     mechanism." Whichever is chosen, the packet's manifest must show the
     full `ls -la`/`getfacl` chain from root to socket, not just the
     immediate directory.
   - Socket itself stays `600`, owned by `hermaroid`, with exactly one
     named placeholder identity granted `rw` via `setfacl`. No
     world-readable socket, no group-wide grant.
4. **Hermes client integration seam — a real supported path, not just a
   generic CLI proof (gap 3):** `cua-driver 0.11.0` supports `mcp
   --socket <path>` in addition to `serve --socket <path>`; Hermes's
   built-in computer-use backend currently only discovers/launches the
   driver's advertised `mcp` invocation and has no documented native
   socket-path setting. This packet's acceptance evidence must include
   **at least one concretely named, actually-supported integration
   path** that Tigwa could use — for example, a narrowly-owned wrapper
   script invoked through the documented `HERMES_CUA_DRIVER_CMD`
   override that runs `cua-driver mcp --socket <path>`, scoped so it only
   picks up the socket during an active hermaroid session. This packet
   does **not** configure Hermes itself (still Tigwa's own work per her
   ACK) — it must prove and document that a real, supported seam exists,
   not hand her an unverified generic-CLI proof and call it equivalent.
   If no supported seam can be found/proven, stop and report that as the
   finding — do not substitute a weaker proof and call it done.
   **Preserved from Tigwa's second review (2026-07-22): the wrapper must
   correctly answer Hermes's initial `manifest` probe as well as the
   later actual MCP launch — both invocation shapes, not just one. The
   live acceptance proof (item 3 of Verification) must demonstrate the
   manifest-probe path succeeding, not only the eventual click/keystroke
   call. If the wrapper can't satisfy the manifest probe, that's the
   same "stop and report" condition as not finding a seam at all — do
   not substitute a generic CLI proof and call it equivalent.**
5. **Documentation for Tigwa's client configuration** (not the
   configuration itself): the exact socket path (full traversal chain
   from item 3), the daemon's command/version, the ACL'd placeholder
   identity name, the Xauthority lifecycle (item 2), the named
   integration seam (item 4), and the revocation contract (what a client
   call returns once `stop` has run — #1670 saw `"Cua Driver daemon is
   not running."`; confirm this stays true here).
6. **Rollback/removal — complete, not partial (gap 4):** a documented,
   idempotent way to remove everything this packet adds: scripts, socket
   and its purpose-created directory, all parent-chain ACL grants from
   item 3, the Xauthority file if present, any wrapper/config artifact
   from item 4, and any stale process/socket state — one command or a
   short numbered list, working even if a session is currently running
   (stop-then-remove).
7. **No flake change**: confirm via `git status` on `~/tgw-flake` on
   a1131 that this packet's work leaves the flake checkout exactly as it
   was before (still just the pre-existing, separately-tracked #1665
   staged-but-unpushed commit, untouched). If anything about this build
   turns out to actually need a flake change, stop and report — that
   contradicts #1670's sizing finding and needs to be re-verified, not
   silently absorbed.

## Verification (live, required before this packet can be marked done)

Reproduce all 5 of #1670's fixture proofs against the **real, persistent**
lifecycle scripts (not the throwaway one-off commands #1670 used),
entirely under default `standard` permission mode, plus 3 new checks for
the gaps above:

1. Screen capture via the daemon after `start`.
2. AX-tree/window discovery — confirm it sees only hermaroid's own
   session content.
3. One benign click + one benign keystroke delivered through the socket,
   via the named Hermes integration seam (item 4 above), not just a bare
   generic CLI call.
4. `stop`, then confirm: a subsequent client call against the same socket
   path fails with the daemon-not-running response; the Xauthority file
   is gone; a raw connection attempt without a valid cookie fails.
5. Isolation: confirm the placeholder client identity (and, separately,
   `hermaroid` itself) cannot enumerate or access `db`'s real runtime
   directory/session (`ls` on `db`'s runtime dir as the relevant identity
   should fail); confirm `db`'s actual live session is unaffected
   throughout (`loginctl list-sessions` before/after).
6. Full traversal-chain evidence: `ls -la`/`getfacl` output from `/` (or
   the purpose-created socket directory's root) down to the socket,
   showing exactly which ACLs were granted and to whom.
7. Trigger-mechanism proof: demonstrate that only the named
   identity/mechanism (item 1's "Trigger identity/mechanism") can invoke
   `start`/`stop` — e.g. confirm `hermaroid` itself cannot self-invoke
   `start` unless that was explicitly the agreed design.
8. `--dangerously-*` flag check: explicit statement + log grep confirming
   no such flag appears in any command this packet's own verification
   run used.

Capture command output/logs for each item, not narrative-only evidence —
this is Tigwa's explicit requirement for the review.

## Out of scope

- Any Hermes/Tigwa-side client code, config, or credential — that is
  Tigwa's own configuration work per her acknowledgement
  (`TIGWA-ACK-hermaroid-client-bridge-ownership-2026-07-22.md`); this
  packet only needs to document what a client would need to know.
- Any change to `db`'s real session, browser, credentials, or Xauthority.
- Any change to the `~/tgw-flake` NixOS configuration (see Spec item 5).
- Any `--dangerously-*` / approval-bypass flag on `cua-driver` (see
  flagged finding above).
- Making the hermaroid session start automatically/always-on — this
  stays on-demand, explicitly triggered.
- Granting `hermaroid` sudo, wheel, or any credential/account/group
  access beyond what #1670 already established as necessary.

## Acceptance for review

A result manifest per the standard `tgw-runner-review` contract, plus:
explicit confirmation that no `--dangerously-*` flag was used anywhere in
this packet's own verification runs, and the 5 proof results with
retained command/log evidence per item above. Tigwa reviews the system-
side contract (socket path, ACL, revocation behavior) before configuring
her own client.
