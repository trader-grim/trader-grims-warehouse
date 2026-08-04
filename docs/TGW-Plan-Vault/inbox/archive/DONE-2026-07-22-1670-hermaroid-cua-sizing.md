# INPROGRESS: #1670 hermaroid graphical session sizing + CUA bridge fixture

PP-CATIONIX-001, todo #1670, follow-on to #1665 (staged-but-not-pushed `hermaroid`
group + Xauthority-ACL design — Tigwa pushed back on that as too broad; NOT touching
or resurrecting it).

Task: on a1131, size whether `hermaroid` (uid 1002, plain test account, no elevated
access) currently has any way to get a real graphical X11 session. If small, build
Tigwa's proposed tighter fixture: cua-driver serve inside hermaroid's own session,
narrow ACL'd Unix socket bridge (not shared Xauthority), explicit start/stop/revoke
lifecycle, test with one benign GTK/text-editor fixture app, prove isolation from
db's real session. Proof fixture only — tear down after, don't leave standing
service running.

Status at time of writing: investigating a1131's flake config (services.xserver /
display-manager) and whether hermaroid is excluded from graphical login today.
No flake edits made yet. If sizing comes back large, will stop and report scope
estimate rather than building.
