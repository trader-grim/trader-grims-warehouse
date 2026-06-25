#!/usr/bin/env bash
# CatioNIX session startup — runs once at graphical session start via Qtile hook.
# Imports X11 + Wayland session vars into the systemd user instance so services
# like tgw-clipd can reach the display without hardcoding env.
systemctl --user import-environment \
  DISPLAY XAUTHORITY \
  WAYLAND_DISPLAY XDG_RUNTIME_DIR XDG_SESSION_TYPE \
  DBUS_SESSION_BUS_ADDRESS 2>/dev/null || true
systemctl --user restart tgw-clipd
