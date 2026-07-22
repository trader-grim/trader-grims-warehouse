# Packet: apps/tgw_app Flutter 3.32.0 API break — DropdownButtonFormField
Todo: #1631   PP: PP-PORTABLE-CATALOG-001   Depends: #1630

## Context budget (ALL the model may load)
This packet + `apps/tgw_app/lib/features/browse/browse_screen.dart` +
`apps/tgw_app/lib/features/item/edit_item_screen.dart`. Nothing else — no
master plan, no other packets.

## Verified live before this packet was written
Building `tgw_app` on a1131 for the first time (2026-07-21, via
`nix-shell -p flutter cmake ninja pkg-config gtk3 clang libsecret sysprof
libepoxy fontconfig`, Flutter 3.32.0 / Dart 3.8.0) failed at
`kernel_snapshot_program` with 4 identical compile errors — all
`DropdownButtonFormField<String>` call sites pass `initialValue:`, which
this Flutter version's `DropdownButtonFormField` constructor does not
accept (only `dropdown.dart:1708`'s real parameter list matched, no
`initialValue` in it). Exact sites:
- `browse_screen.dart:271` — `initialValue: _selectedLocation`
- `browse_screen.dart:292` — `initialValue: _selectedStatus`
- `edit_item_screen.dart:163` — `initialValue: values.any(...) ? controller.text : null`
- `edit_item_screen.dart:340` — `initialValue: _selectedCondition`

`pubspec.yaml`'s `environment: sdk: ">=3.0.0 <4.0.0"` is broad and does not
pin a Flutter framework version, so bumping/pinning Flutter is not the
fix — the app code needs to match the widget's current API.

## Spec
Rename `initialValue:` to `value:` at all 4 call sites above. No other
change — do not touch surrounding logic, state variables, or any other
widget in either file.

## Out of scope
- Any other `.dart` file.
- Any dependency-version bump in `pubspec.yaml`/`pubspec.lock` (the "44
  packages have newer versions incompatible with dependency constraints"
  warning from `flutter pub get` is a separate, pre-existing condition —
  do not touch it here).
- Any other build warning seen during this session (e.g. the repeated
  `sysprof-capture-4 was not found in pkg-config` message during the cmake
  probe step) — that did not block the build and is not part of this
  packet's scope.

## Acceptance
Re-run the build on a1131:
```
cd /opt/TGW/src/trader-grims-warehouse/apps/tgw_app
sudo -u tgw nix-shell -p flutter cmake ninja pkg-config gtk3 clang libsecret sysprof libepoxy fontconfig --run 'export CC=clang CXX=clang++; flutter build linux --release'
```
Live evidence: the build must reach `Built build/linux/x64/release/bundle`
(or equivalent success line) with zero `ERROR:` lines, not just "no
initialValue errors" — a fresh full build, not a partial recompile.
