# Heads-up — PP-RADAR-001 clipboard direction

**From:** Tigwa, relaying Dave's direction
**Date:** 2026-07-19
**Status:** Directional heads-up / design input; **not** build authorization
**PP:** PP-RADAR-001
**Linked todo:** #1573 (Tigwa-owned librarian proposal)

## Settled direction

Radar is to become the proper replacement for insecure network clipboard sharing:

- server-based, encrypted, and delivered directly to a selected named host/device, in the interaction spirit of `kdeconnect-cli`;
- no ambient OS-clipboard capture, mirroring, sniffing, broadcast, or persistent network spew;
- networked clipboard movement occurs only through explicit Radar `copy` / `send` / `pick` operations addressed to the selected recipient;
- each delivery needs authenticated/encrypted transport, a receipt/audit record, expiry/cleanup semantics, secret exclusion, and local recipient insertion only when its approved action contract permits it.

This supersedes the earlier framing of `tgw-clipd` as a primary interface. A clipboard-linked adapter may exist later, but only as a deliberate local input/output boundary into Radar.

## Broader Radar context

Radar is the librarian/operator layer, not a redesign of TGW substrate. It uses the established ecosystem:

- git-annex: authoritative file identity, content hashes, versions, availability and redelivery;
- Syncthing: selected active artifact-view distribution, including Android;
- Flutter: operator UI;
- Tailscale: private reachability;
- Recoll and NATS JetStream: broader TGW retrieval/event substrate.

Radar compiles the precise current-entry context server-side from authoritative sources, cheaply, and returns only the relevant context/tools to clients. For a SKU, that means the applicable title/price plus direct Flutter, eBay, history, solds, and Complete Toolkit actions, rather than broad client-side data scraping.

Files/charts and other artifacts are not forced through entry text: they are annex-versioned and can be delivered through the approved Syncthing view. When changed, prior delivered versions are archived with hash/provenance; the new version becomes active and remains re-deliverable.

## Requested handling

Treat this as design context for future PP-RADAR-001 work. Do not build, change services, configure Syncthing/KDE Connect, or broaden clipboard collection from this heads-up. The Tigwa-owned #1573 proposal will bring Dave the precise data/action/transport contract for review.
