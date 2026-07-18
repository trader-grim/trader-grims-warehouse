# PP-ROUTER-001 — D-Link DIR-868L router into the TGW ecosystem (full detail)

## PP-ROUTER-001 — D-Link DIR-868L router into the TGW ecosystem
**RECOVERED 2026-07-16/17** — real research existed at `docs/ai-plans/
router-dlink-dir868l-ecosystem.md` (filed 2026-07-06) but had never been
given a PP number or a master-plan mention, found during a "recover lost
PPs" sweep Dave requested. **DD-WRT confirmed the correct firmware**
(OpenWrt doesn't support this Broadcom chipset) — known flash path
documented, one known post-flash 5GHz quirk with a documented fix. Six
candidate capabilities once flashed, not prioritized: complete the DHCP
reservation audit, VLAN-isolate intake/camera devices, fold router health
into `tgw health`/ops-digest, authoritative local DNS, WireGuard, DR
backup of router config (todo #1491). **Live finding surfaced by the
recovery, not just historical:** the DHCP table has two different MACs
both claiming `192.168.60.112` under the name "hpi3" — real unresolved IP
conflict, todo #1490.

**Status, 2026-07-17 (Dave): still just a proposal, no flash decision
made.** **Decision scope, narrowed same day (Dave):** the actual binary
choice is only "leave it D-Link-proprietary, or take advantage of the
diminutive but overpowered box" — i.e. flash or don't. It is NOT a
commitment to build all 6 candidate capabilities at once. Once flashed, a
package manager (Dave: "optware or whatever" — Entware is Optware's
actively-maintained successor and the one to actually evaluate) lets
services get added **one at a time**, same incremental-progress discipline
as the rest of the plan (see "parallel-track discipline" near the R1
table) — each candidate capability becomes its own small packet whenever
it's next in line, not a single big router-rebuild project gating on all
6 landing together. Nothing in this PP is actionable ahead of the
flash-or-don't decision itself; the IP-conflict fix and the NATS/
alarm-system leg below both wait on it too, not on each other.

**Possible NATS JetStream-for-alarm-system leg, 2026-07-17 (Dave):**
"unrelated to our other use" (i.e. distinct from PP-AIOPS-001's JetStream
audit-stream design) — Tigwa has already done some research into running
NATS JetStream on this router as part of an alarm-system design. My
router findings sent to her (`inbox/tigwa/CLAUDE-NOTE-2026-07-17-router-
findings-for-nats-alarm-research.md`) for reconciliation with whatever
she already has — 256MB total RAM on this hardware is the concrete
constraint her research should be checked against. Not yet merged into
one design.

