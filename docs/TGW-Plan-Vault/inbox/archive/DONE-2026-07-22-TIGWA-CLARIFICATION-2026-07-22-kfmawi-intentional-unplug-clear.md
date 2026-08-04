# Clarification — intentional KFMAWI unplug must be one-touch clearable

**To:** Claude
**From:** Tigwa
**Status:** Dave-set interaction requirement; no implementation authorization

Keep the KFMAWI power-out alarm simple. Dave needs to be able to intentionally unplug the device occasionally and clear that alarm locally.

Required interaction:

- On charger-loss, show/sound the local power alarm and offer one obvious control: **Intentional unplug / Clear alarm**.
- Activating it suppresses only the current charger-loss incident until charging returns. It does not disable future outage detection or alter any TGW monitoring policy.
- If not cleared, the normal configurable debounce/outbound outage alert proceeds.
- When charging returns, clear the suppression/incident automatically and resume normal monitoring.
- No multi-step maintenance mode, credentials, remote approval, or ceremonial acknowledgement is needed for this ordinary physical action.

The drill must cover both paths: a real unacknowledged unplug sends the labelled outage/restoration alerts; an intentional local unplug is visibly cleared and does not create repeated noise.
