# DONE — PP-PHOTOSYNC-001 P3 (todo #1118): C10 detector

`tests/test_operator_origin_sourcescan.py` added — source-scan (fence-grep-audit
pattern) over every `state_machine.enqueue_job(` call in `http_server.py`.
Passes clean on the current tree (25 sites); a synthetic poisoned site
correctly fails. `invariants.md` C10 updated 🔶→✅. Tests-only packet, no
live-fire needed (PD4 N/A — detector, not behavior change). Detail in
`plan/pp/PP-PHOTOSYNC-001.md` P3.
