---
name: tgw-packet
description: Execute one TGW work packet with the execution-track discipline — tight context budget, live ground-truth verification BEFORE changing anything, exact-spec execution, live acceptance evidence after. Use when the user says /tgw-packet <todo-id>, asks to execute/run a packet, or hands over one scoped todo from an execution track.
---

# TGW Packet Execution

Execute exactly ONE work packet (one todo), the way the R-track sessions that
worked went: small scope carved out of the big plan, ground truth checked live
before work starts, and nothing called done without observable evidence.

This skill exists because sessions that loaded the whole plan and trusted its
assumptions drifted, while packet sessions with pre-flight live verification
went (Dave, s45) "extremely well." Do not dilute the steps.

## Usage

> /tgw-packet <todo-id>

## Steps

### 1. Load the packet — and ONLY the packet

- `sudo -u tgw tgw todo brief <id>` for the task brief.
- If `docs/TGW-Plan-Vault/plan/packets/<id>-*.md` exists, read it; its
  **Context budget** section is a HARD ceiling on what you may load.
- Do NOT read the full master plan, FUTURE-IDEAS, or unrelated reference docs.
  Easing the burden of the huge plan is the point of packets. If the packet
  cites no context budget, load at most: CLAUDE.md constraints (already in
  context), the one PP doc it names, and the specific code paths it touches.
- Mark the todo `in_progress`; drop `inbox/INPROGRESS-<id>-<slug>.md`.

### 2. Pre-flight: verify the packet's assumptions LIVE — before any change

The packet was written in a planning session; the world may have moved.
Before writing a line of code, verify every load-bearing assumption against
the authoritative source, not local flags or docs (invariant C11):

- Claims about eBay state → fresh, uncached API read (offer/listing/policy),
  never the local mirror alone.
- Claims about data shape/fields → open 2–3 real item JSONs and check the
  actual field semantics against the real consumer of the field
  (feedback-recompile-not-oneshot: the attribute_set vs ebay_category_id
  mistake came from skipping this).
- Claims about pipeline behavior → `journalctl` / `queue_jobs` for what it
  actually did recently, not what the code suggests it would do
  (feedback-verify-before-blaming-external: "look at the damned log").
- Claims that an error/rejection is novel → grep the existing dead-letter,
  blocked and incident registries for the exact message first
  (feedback-check-history-before-building).

If ANY assumption fails verification: STOP. Report the mismatch to Dave and
update the packet — do not silently adapt the spec to the new reality.

### 3. Execute exactly what the packet specifies

- Every cadence, TTL, limit, default comes from the spec. Anything unstated
  is NOT delegated: if you must choose, flag the choice explicitly in your
  report (PRIME DIRECTIVE 3; silent substitutions caused real outages).
- Respect the packet's **Out of scope** list — the adjacent broken thing you
  find gets a `tgw todo --add` and a mention, not a fix.
- Flag any new/removed metered API calls (eBay pools, LLM) as you go
  (feedback-api-quota-flagging), and check `/opt/TGW/var/run/thermal.status`
  before heavy scans. Heavy checks (test suites, sweeps) run on a1131
  (`ssh claude@192.168.60.101`) when thermal is a concern.

### 4. Acceptance: live evidence, then done

- Run the packet's **Acceptance (live)** command/URL/SKU on real data.
- "Tests pass" is necessary, never sufficient (PRIME DIRECTIVE 4). Done means
  the observable result — URL, log line, item JSON diff, fresh eBay API read —
  is captured in your report for Dave.
- Where the change touches live listings, verify in BOTH directions when
  reversible (apply → confirm on eBay → revert → confirm reverted), using the
  safe test-item technique when a throwaway is needed
  (feedback-safe-test-item-technique).

### 5. Close the loop

- `sudo -u tgw tgw todo --done <id>`; rename the inbox note to
  `DONE-<id>-<slug>.md` with: what shipped, the live evidence, deviations
  flagged, and any out-of-scope finds filed as todos.
- Commit if (and only if) Dave has authorized commit-as-you-go this session.

## Constraints

- One packet per invocation. If the packet turns out to be two packets, split
  it (report back) rather than stretching the session.
- Never alter eBay OAuth scopes; never bypass the tgw-api fence; secrets stay
  in secrets_root.
- A worker skip/guard hit during verification is a finding to persist, not a
  log line to move past (invariant C11).
