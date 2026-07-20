# Addendum: wired Ethernet reduces clipboard-event transport risk

**State:** `capture-staged` — Dave operator/environment evidence; no build, network change, or release authorized
**Date:** 2026-07-20
**Related:** Android clipboard-history constraint and clipboard product hypotheses

Dave notes that the collection-device environment has wired Ethernet. Therefore the initial solution does not need to solve unreliable mobile, Bluetooth, or public-internet transport as a prerequisite.

This materially narrows a possible first implementation/validation boundary to a local-LAN, explicit-event model:

- an operator intentionally emits a selected value/event;
- the receiving device/system is explicitly named or selected;
- event delivery, receipt, expiry, and history are visible and recoverable;
- transport can remain local to the wired collection network.

Wired Ethernet reduces transport reliability and discovery complexity. It does **not** itself solve Android’s restrictions on clipboard observation/history, nor does it authorize hidden clipboard capture. The product question remains whether explicit event/history semantics can replace the required collection workflow while complying with Android and distribution policy.
