# Request: wire your a1131 MCP access, then tell us what you actually need

**From:** Claude (relaying Dave's direction, 2026-07-18)
**Todo:** #1505, pp_ref PP-HERMES-EA-001
**Related:** todo #1342 (never-built a1131 MCP wiring), #1459 (open SSH-scope finding — do NOT touch that credential yourself, see part 3)

## Context

Dave confirmed today: it's on us to finish #1342 — the tgw MCP wiring for your
a1131 side was left for you to self-configure back on 2026-07-12 and never
happened, so the only way you currently reach `tgw` at all is raw SSH shell
into tgw-prod. That's backwards — the MCP layer is the one actually built to
express and enforce a scope; raw shell is the no-limits emergency fallback,
not the everyday path. Dave wants routine work (starting with helping him
update items) to go through MCP, with raw SSH reserved for things like
snapshot/shutdown orders he telegraphs through you directly.

## Part 1 — apply the already-decided wiring

This part isn't a new design question — Dave already chose the transport on
2026-07-12 (see `pp/PP-HERMES-EA-001.md`, "MCP access for Tigwa's a1131
tools" section, "RESOLVED" subsection): **SSH-tunneled, read-only tool
scope**, reusing your existing key into `db@tgw-prod` — chosen specifically
over a LAN-listening MCP server to avoid a new network attack surface.
tgw-prod's own Hermes-lite side already runs this exact pattern, live and
verified:

```
hermes mcp add tgw --command sudo --args -u tgw env TGW_MCP_READONLY=1 \
  /opt/TGW/.venvironments/tgw/bin/python -m tgw.mcp_server
```

Your a1131-side equivalent tunnels the same invocation over SSH instead of
running it locally:

```
ssh db@tgw-prod sudo -u tgw env TGW_MCP_READONLY=1 \
  /opt/TGW/.venvironments/tgw/bin/python -m tgw.mcp_server
```

Wire this into whatever your local AGY/MCP config equivalent is (mirroring
`~/.gemini/config/mcp_config.json`'s `command`/`args` shape on tgw-prod).
`TGW_MCP_READONLY=1` drops `tgw_enqueue`/`tgw_add_suggest` from tool
registration entirely (not just hidden client-side — verified live,
readonly mode registers 8 tools vs 10 in full mode), matching your current
"still in training" authority model. Report back once connected + tool
count confirmed (same verification Hermes-lite did: `hermes mcp list` or
your equivalent).

## Part 2 — propose what access you actually need

Once wired read-only, look at the actual jobs Dave has assigned you (plan
review, inbox/librarian duties, thermal monitoring policy work, admin-file
handling, whatever else is currently yours) and tell us: which of those jobs
are you currently blocked on, or doing the hard way via raw SSH, because the
read-only MCP scope doesn't cover it? Be specific — name the job, name the
tool/capability it needs (e.g. "enqueue an ai_identify job for a re-drafted
item" or "set an item field via the fence"), not a request for blanket write
access. This is the same pattern as your HR-001 contract work — you scope
the proposal, Dave/Claude review and grant deliberately, not the other way
around. **Do not self-grant anything** — this part is a request document,
not an implementation.

One concrete job Dave specifically wants covered: helping him update items
(this is the immediate practical need driving this whole request). Please
include in your proposal exactly which item-update actions you'd need
scoped access to (e.g. a bounded subset of `tgw_enqueue`'s queue names, or a
new narrower tool if the full enqueue surface is more than this job needs).

## Part 3 — the SSH credential itself (#1459) — do not touch, just inform

Separately flagged, still open: your current key into `db@tgw-prod` grants
unrestricted shell + `db`'s passwordless `sudo`, far beyond "notify/interrupt
only, never pause/kill/shutdown." Dave's explicit call: rescope this if
appropriate, **but not blindly** — he doesn't want the raw emergency
capability (ordering a snapshot, a shutdown, etc. when he tells you to)
accidentally cut off by an overly narrow forced-command restriction applied
before anyone actually knows the full list of legitimate uses. Your Part 2
proposal is exactly the input needed to scope this correctly later — once
we know everything you legitimately need (both MCP-side and any raw-SSH
emergency actions Dave still wants to reach through you), Claude/Dave will
design the actual credential scoping as a separate, deliberate step. Nothing
for you to build or change on the SSH side yet — just make sure your Part 2
list is complete enough to inform it.

## Part 4 — report delegation to save tokens

Dave wants your *regular* reports to him (routine status, not urgent
incidents) to route through Tigwa-lite (tgw-prod) instead of you composing
and sending them directly — Tigwa-lite already has the cheaper-model
Telegram channel (#1346) and some of the "report Tigwa active-work/status"
groundwork (#1432). Design how this handoff should work: what counts as
"regular" vs. something that still needs to come from you directly, and how
you'd hand a report over to Tigwa-lite for delivery (a shared file/queue she
polls? a direct message? something else?). This is your design to propose,
not a spec Claude is handing you — same pattern as the rest of this note.

## Out of scope for this note

No SSH credential changes, no self-granted MCP write access, no changes to
the thermal-response authority model. This is: wire the already-decided
read-only transport, then tell us what more you need and how the reporting
handoff should work — Dave/Claude decide and build anything beyond that.
