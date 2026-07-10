## PP-PERP-AUTO-001 — Perplexity Semi-Automation Interface

### Problem
Submitting research briefs to Perplexity requires manual copy-paste: open brief → copy prompt
→ switch to browser → paste → wait → copy result → save to inbox. For 5+ briefs, this is 30+
minutes of mechanical work. Even Perplexity's API doesn't expose the Pro search quality.

### Simplified workflow (session 10 — no scraping required)
Perplexity's three-dot menu → "Download as Markdown" is the key insight. No HTML scraping needed:
1. Paste prompt → press Enter → wait for completion (watch browser)
2. Three-dot menu → Download as Markdown
3. Move `.md` file to `inbox/` → PM-intake processes automatically
4. For multi-turn: download → read result → ask follow-up → download again

This is already low-friction. ydotool can automate steps 1–2 (paste + submit + trigger download)
but step 3 (moving the downloaded file) can be handled by a file watcher on `~/Downloads/`.

### Automation approach: ydotool + file watcher
Semi-automate using `ydotool` (Wayland) or `xdotool` (X11):
1. `tgw perp-run PERPLEXITY-001` — reads brief, extracts prompt, pastes + submits via ydotool
2. Operator watches Perplexity complete (automation cannot reliably detect this)
3. Operator triggers download (three-dot menu or keyboard shortcut)
4. File watcher (`inotifywait` on `~/Downloads/`) moves `*.md` to `inbox/` automatically
5. PM-intake picks it up on next session startup

### Infrastructure recommendation (session 10)
Use the **tmux/ltsp/qtile/ssh stack** for dependability:
- Run the Perplexity browser tab in a dedicated Qtile workspace (workspace 3 "ebay" or a new "research" workspace)
- A dedicated Qtile scraping layout can control window focus and viewport for automation
- SSH + tmux enables remote triggering without being at the physical machine
- LTSP: remote desktop to the Perplexity workspace from tablet during coffee sessions

### Qtile scraping layout concept (session 10)
A custom Qtile layout that locks focus to the browser window and exposes automation hooks:
- Super+T → p: enter "Perplexity mode"; bar shows brief name; chord keys: `r`=run, `d`=download, `n`=next brief
- Could also handle token renewal automation (paste token, confirm) — same ydotool pattern

### Limitations
- Perplexity completion detection is not automated — operator confirms when done
- ydotool approach is best-effort; window focus can break if anything else steals focus
- Iterative research (ask → download → read → ask more → download) is semi-manual but fast

### Track 4 (Operator) task
This is an operator tool, not a background worker. Priority 3 in Track 4.

---

