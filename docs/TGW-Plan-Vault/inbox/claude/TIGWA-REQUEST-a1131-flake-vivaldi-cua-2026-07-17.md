# Request: batch a1131 flake update for Tigwa Vivaldi/CDP and native desktop control

**From:** Dave via Tigwa
**Date:** 2026-07-17
**Owner requested:** Claude / a1131 flake maintainer
**Priority:** normal — stack this with other justified a1131 flake changes; do not make a one-off rebuild solely for this request.

## Why

Dave uses Vivaldi custom context menus as one way to execute `tgw.source` macros. The browser-control path should therefore use Vivaldi, not require a separate Chromium browser.

Tigwa has completed the user-owned portion under the `tigwa` account:

- Vivaldi is already system-installed and available at `/run/current-system/sw/bin/vivaldi` (7.6.3797.58).
- A separate Tigwa-owned Vivaldi profile runs headless with CDP bound to loopback `127.0.0.1:9222`.
- Hermes is configured to attach to that local CDP endpoint.
- `cua-driver-rs 0.8.3` is installed under `/home/tigwa/.local/bin/` with telemetry disabled.

Native desktop control cannot currently start because `ldd /home/tigwa/.local/bin/cua-driver` reports these missing runtime libraries:

```text
libX11.so.6
libXi.so.6
libxkbcommon.so.0
```

## Requested batched flake change

On the next appropriate a1131 flake update, make these runtime libraries available to the `tigwa` account/environment:

```nix
xorg.libX11
xorg.libXi
libxkbcommon
```

Retain Vivaldi availability for `tigwa`; do **not** add Chromium merely for Hermes CDP. Vivaldi is Chromium-family and the selected browser.

## Boundaries

- Do not modify TGW source, queues, services, bots, credentials, or eBay state.
- Do not attach Tigwa to `db`'s existing graphical browser/profile/session.
- Do not change the existing dedicated-profile/CDP loopback-only separation.
- This request is intentionally batchable, not urgent.

## Acceptance evidence

After the batched rebuild, provide:

1. `ldd /home/tigwa/.local/bin/cua-driver` showing no missing `libX11`, `libXi`, or `libxkbcommon` dependency.
2. `cua-driver --version` executed successfully as `tigwa`.
3. `vivaldi --version` executed successfully as `tigwa`.
4. Any remaining graphical-session/Wayland or accessibility permission requirement stated plainly, without routing Tigwa into `db`'s session.

This is a foundation request only. Visible Vivaldi custom-menu use and any authenticated browser-profile decision remain separate follow-up decisions.
