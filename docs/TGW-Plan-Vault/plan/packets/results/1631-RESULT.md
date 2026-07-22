# Result: 1631 tgw-app-dropdown-initialvalue
Status: partial
Todo: #1631   PP: PP-PORTABLE-CATALOG-001
Files touched:
- apps/tgw_app/lib/features/browse/browse_screen.dart
- apps/tgw_app/lib/features/item/edit_item_screen.dart

Live evidence: Edit applied and diff verified to match the packet's Spec
exactly — 4 sites, `initialValue:` renamed to `value:`, no other lines
changed:
- browse_screen.dart:271 `initialValue: _selectedLocation` -> `value: _selectedLocation`
- browse_screen.dart:292 `initialValue: _selectedStatus` -> `value: _selectedStatus`
- edit_item_screen.dart:163 `initialValue: values.any(...) ? controller.text : null` -> `value: values.any(...) ? controller.text : null`
- edit_item_screen.dart:340 `initialValue: _selectedCondition` -> `value: _selectedCondition`

This worktree's environment (tgw-prod, run as `db`) has no `flutter`
binary and this task was not run as `tgw`/on `a1131`, so the packet's
Acceptance step (`sudo -u tgw nix-shell ... flutter build linux --release`
on a1131) was NOT run here. Status is `partial` on that basis — the
mechanical edit is done and verified by diff inspection, but live build
verification still needs to run on a1131 per the packet before this can
be marked fully accepted.

Deviations from spec: none.
Out-of-scope findings filed: none.
