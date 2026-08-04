# PP-HARDWARE-001 — IT / hardware track (full detail)

## PP-HARDWARE-001 — IT / hardware track (drive-space re-evaluation absorbed) — NEW 2026-07-11
**Dave, triaging #1136: "it and #1136 and similar need an IT or hardware
PP."** Previously PP-HARDWARE-001 was only referenced by name from other
docs (GPU upgrade), never had its own heading. Governing philosophy:
"we get it running, we make money, we get server. We no make money we use
this thing" — bootstrap hardware until revenue justifies real
infrastructure. Near-term concrete plan (Dave's own words): M.2-to-SATA
adapter to bring a 1TB USB SSD onto the board replacing an HDD; a 4-bay
SSD enclosure + 4 spare SSDs for a real storage tier; heat sinks on the
SSDs. **Open, unresolved, flagged for a dedicated pass:** where should
knowledge-hub work (PP-KNOWLEDGE-001) physically live so it doesn't fill
`/opt/TGW` — the existing tiered-remote design (PP-ANNEX-001, the
power-tiered drive inventory below) points away from the NVMe but this
hasn't been explicitly confirmed for this specific question; and Dave's
own ask for "a real analysis of what we need, what we want, what we will
need" — not done, this PP is the placeholder for it, not a substitute.
Full design: `pp/PP-HARDWARE-001.md`.

**Dave: "put revaluation item into plan for drive space."** Todo #1056
(extend `vg_tgw` into HDD space) turned out blocked on a stale premise:
checked live `lsblk`/`pvs` — sdb no longer appears in the disk list at
all, and sdc (the other candidate) was fully repartitioned and put into
active service the same session for backup infra (`sdc1`=tgw-db-backup,
`sdc2`=tgw-itemdata-snap, `sdc3`=tgw-itemarchive). No free/unclaimed disk
currently exists to grow `vg_tgw` into (PV `nvme0n1p2` has 96MB free), and
`reference/DRIVE-REGISTRY.md` itself is stale against today's real layout
(doesn't reflect sdc's repartition, `TGW-VAULT`, or several other drives
now in service). **Needed:** a full physical-disk-fleet audit + registry
refresh, then a fresh decision on where `vg_tgw`/nix growth room comes
from — new hardware, or an explicit repurpose of something already in
service. Not started; the original LVM-expansion plan (sdb/sdc as
candidate PVs) is superseded by this finding.

**Real current pressure (checked 2026-07-04):** not `/nix` (52% used, 33G
free, fine) — `/opt/TGW` (nvme, ItemData/ItemCatalog/incoming) is at
**83% used, only 48G free**, and `ItemData` alone is already 180G for 55K
items. Dave: "I have half a million items here ready to process" once
the pipeline is fixed — heading toward that ~9x scale, this is the
partition that will actually run out first.

**Power constraint (Dave, 2026-07-04):** generator-powered — prefer
drives that can come offline when not needed. Real drive inventory
mapped (`lsblk` + `TRAN`/model): `nvme0n1` (internal NVMe) + `sda`
(internal SATA HDD) can't be unplugged but draw modest power; `sdc`
(700G) + `sdi` (465G, currently idle) are 2.5" USB laptop drives —
bus-powered, no external brick, the reliable always-on tier; `sdd`
(MasterArchive, 1.8T) + `sdh` (tgw-backup, 931G) are 3.5" drives in a
powered dock — connect only when actively syncing, matches the existing
PP-BACKUP-001 A7 "rotating offline drive tier" design exactly, just
applied for power reasons too, not only DR rotation. Planned upgrade:
a 4-bay USB3 NVMe dock (bus-powered, low-heat) — Dave has the SSDs
already, multi-terabyte capacity once built, likely retires the need to
keep `sdd`/`sdh` connected as often.

**Merged with PP-DRIVE-INDEX-001** (see below) — recoll-driven dedup
across the already-mounted data is the near-term space-recovery lever,
before deciding what (if anything) to offload onto `sdi`.

Audited sdb/sdc live: sdb absent, sdc repartitioned into backup services. No free disk to grow vg_tgw. Closing #1056 as superseded, opened #1136 for re-evaluation.
