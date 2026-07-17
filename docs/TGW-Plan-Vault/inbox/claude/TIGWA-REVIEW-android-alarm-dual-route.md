# Review request — Android alarm dual-route design

**Artifact:** `reference/TGW-Android-Alarm-Dual-Route-Design-2026-07-15.md`  
**Tracker:** #1385 / PP-HERMES-EA-001

## Decision recorded

HTTP API is primary. A KDE Connect route is the independent secondary leg: fixed remote command when configured/tested, otherwise explicit D-Bus clipboard envelope. Both use one incident ID and Tasker-side deduplication.

## Review questions

1. Approve parallel dual delivery for elevated/critical events, with one Tasker presentation per incident ID?
2. Is `raise | test` the correct initial allowlist, excluding generic task execution and `clear`?
3. When KFMAWI exposes a named remote command, should it replace clipboard fallback after its reboot test passes?
4. What device-side receipt/ack is practical with the existing Tasker estate?

## Evidence

- KFMAWI was live/reachable through a1131 `db` KDE Connect session at review time.
- `kdeconnect-cli --send-clipboard` accepts only the current desktop clipboard; explicit payload delivery is the verified D-Bus `sendClipboard(string)` route.
- No KFMAWI KDE remote commands were exposed during read-only inspection.
- No command, clipboard event, alarm, ADB operation, or device setting was changed.
