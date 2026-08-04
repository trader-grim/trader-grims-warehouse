# Clarification — Dave is the active human-in-the-loop, not a notification endpoint

**To:** Claude
**From:** Tigwa, librarian
**Status:** Dave-set interaction requirement; no implementation authorization
**Applies to:** human inbox, Flutter, ntfy, Tasker, and any reminder/alert packet.

Dave's existing working security/operations practice is that he reads and handles messages. He can monitor communications. The human-in-the-loop is an active operator responsibility, not a checkbox requiring the system to coerce acknowledgement or silently automate past him.

Design consequence:

- The Flutter inbox must make messages and their status legible enough for Dave to monitor and act on normally.
- ntfy/Tasker/KDE Connect only improve timely visibility and navigation to that inbox; they do not replace Dave's review or manufacture a second decision channel.
- A notification dismissal, phone lock, delayed read, or absence of a click is not consent, completion, escalation approval, or permission for an agent to proceed.
- Acknowledgement/snooze controls are aids for the cases where state matters; they are not a requirement to turn every ordinary message into workflow bureaucracy.
- The system should report genuine delivery/integrity failures and significant due/red conditions, while remaining quiet when healthy. It should not nag Dave merely to prove that a human exists.

The operational design objective is therefore: improve Dave's normal message-reading loop with durable context and clear exceptions, while retaining Dave as the explicit authority for consequential decisions and outward actions.
