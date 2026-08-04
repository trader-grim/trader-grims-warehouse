Working todo #1292 + #1293 (PP-COHESION-001) on branch
`todo/1292-1293-clipd-rofi-picker`: fixing `launch_rofi_picker()` in
`src/tgw/clipd.py` — it queries a nonexistent `clips` table (should be
`clip_history`, per `clip.py`'s real schema) and also has a double
`cursor.fetchone()` call on a LIMIT 1 query that returns None on the
second call. Per the packet, both bugs stack on the same function and
must be fixed together for any live acceptance evidence to be possible.
Next: read `clipd.py`/`clip.py`, apply the fix, verify live with a test
row in `clip_history`, write the result manifest.
