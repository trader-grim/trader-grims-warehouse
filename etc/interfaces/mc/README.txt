TGW — Midnight Commander Virtual Filesystems
============================================

Open these sentinel files in MC to enter the corresponding live VFS:

  catalog.tgwcat     — entire item catalog, organised by location (55k+ items)
  queue.tgwqueue     — PostgreSQL job queue, live snapshot by state
  health.tgwhealth   — platform health checks (Postgres, Ollama, eBay token…)
  services.tgwsvc    — TGW systemd services: active/ and inactive/
  logs.tgwlogs       — read-only journalctl per worker (last N lines, capped)

Inside each VFS, pressing Enter (or F3/View) on any file shows its content.
Files marked executable (x bit) open in less when you press Enter.

tgwitem VFS — automatic on any SKU JSON
----------------------------------------
Navigate to any ItemData SKU directory (e.g. /opt/TGW/data/ItemData/tgwXXX/)
and press Enter on the tgwXXX.json file.  You'll see:

  meta.json          — full item record
  fields/            — one .txt per field (Enter opens in less)
  photos/            — actual image/video files (F3 to view in mcdisplay)

tgwcatalog VFS
--------------
Enter catalog.tgwcat.  Top level shows 2800+ location directories.
Navigate into a location to see items.  Item filenames encode:

  {sku}__{status}__{title}.item

Enter on an item opens its full ItemData JSON in less.

tgwqueue VFS
------------
Enter queue.tgwqueue.  Subdirectories match queue states:
  queued/  running/  retry_wait/  dead_letter/  succeeded/  cancelled/
Each file is {queue}__{operation}__{job_id_prefix}.json.
Enter opens the full job record in less.

tgwhealth VFS
-------------
Enter health.tgwhealth.  Files named:
  OK___check_name.txt   — check passed (FAIL sorts before OK alphabetically)
  FAIL_check_name.txt   — check failed
  _status.txt           — overall OK/FAIL + elapsed
  _full.json            — complete health JSON

tgwservices VFS
---------------
Enter services.tgwsvc.  active/ and inactive/ subdirs.
Enter on a service runs  systemctl status <service>  in less.

tgwlogs VFS
-----------
Enter logs.tgwlogs.  Files named:
  _summary.txt       — every worker + its systemctl is-active state
  {queue}.log        — last N journal lines for tgw-worker@{queue}.service
Enter opens the journal in less.  Read-only; output is capped at
TGWLOGS_LINES lines (default 500, max 5000).  If a log is empty/denied the
tgw user likely needs to be in the 'systemd-journal' or 'adm' group.

Extfs scripts location: ~/.local/share/mc/extfs.d/
Sentinel files:         /opt/TGW/mc/
MC extension config:    ~/.config/mc/mc.ext.ini
