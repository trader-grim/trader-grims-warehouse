## PP-TASKER-001 — Android Tasker + Join Integration

### Goal
Evaluate and design TGW automation opportunities using Tasker (automation app) and Join
(Tasker's push-notification sibling, similar to KDE Connect). Dave has a Tasker license
and a Join license.

### Join evaluation
- Join is an alternative to KDE Connect for Android↔desktop push/pull
- Capabilities: push notifications, clipboard sync, SMS forwarding, file transfer, URL open
- TGW use: push "item staged for review" notifications to phone; receive barcode scans
- Compare to KDE Connect: Join works via cloud (not LAN); better when phone not on same network
- Evaluate: which offers better reliability for `SETTEMPLATE:` clipboard relay from tgw.source?

### Barcode scanner — confirmed available
Dave has a fast commercial barcode scanner app on the camera phone. The existing Tasker app
can already open it. Need to audit available broadcast/activity intents to capture scan output
(likely Intent → StartActivity or BroadcastReceiver → Tasker Variable). Actionable first step:
check what intents the scanner exposes; wire to Tasker → Join/KDE Connect → tgw-http intake.

### Tasker opportunities
- **Barcode scan → intake**: Tasker opens commercial barcode scanner (intent audit needed) → capture result → POST to tgw-http intake endpoint
- **Voice → suggest**: Tasker microphone → Whisper → `tgw suggest`; or Tasker built-in voice
- **Photo trigger**: Tasker camera trigger → sends image to TGW intake folder via Join/KDE Connect
- **Notification response**: tgw-http push → Tasker task (tap "approve" → POST publish action)
- **USB scale auto-read**: Tasker OBD plugin or USB serial reader for scale integration on Android
- **Custom intake flow**: Tasker UI screen with SKU scan + template select + size entry; posts to tgw-http

### Tasker vs KDE Connect architecture decision
Currently KDE Connect is primary (clipboard relay, file share). Evaluate whether Join can
replace or supplement it. Key question: does Join support `wl-copy` / `wl-paste` clipboard
injection the same way KDE Connect does? If not, KDE Connect stays for clipboard relay.

### Dependencies
- PP-REMOTE-001 (tgw-http reachable from phone)
- PERPLEXITY-005 (Syncthing + KDE Connect research may cover Join as well)

---

