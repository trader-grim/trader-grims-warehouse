---
title: TGW Shell Source Audit (PP-SHELL-001)
markmap:
  colorFreezeLevel: 2
  initialExpandLevel: 2
updated: 2026-06-05
---

# Shell Source Audit

## Overview

Two files: `/opt/TGW/bin/tgw.source` (3406 lines) and `/opt/TGW/bin/tgw-dev.source` (158 lines).
Dispositions: **KEEP** (still active) · **WRAP** (replace body with `tgw` CLI call) ·
**ARCH-VIOLATES** (writes ItemData directly — should route through `tgw` CLI) ·
**DEPRECATED** (superseded; flag for future removal pass) · **DONE** (already fixed this session)

## tgw-dev.source status

### Already fixed (session 6)
- `tgw-rebuild()` → `tgw build-all` ✅ DONE
- `tgw-build-searchcatalog()` → `tgw build-search` ✅ DONE
- `tgw-browser-dev()` → `tgw-browser` ✅ DONE

### KEEP (still useful)
- `tgw-dev-venv()` — activates TGW venv
- `tgw-dev-info()` — shows environment vars
- `tgw-dev-py()` — runs Python in venv
- `cdtgw()` — jump to `$TGW_ROOT`
- `cditems()` — jump to `$TGW_ITEMDATA_ROOT`

## tgw.source — KEEP (interactive / session-specific)

### Environment setup
- `tgwpath`, `itemdatapath`, `catalogpath`, etc. — path variables (lines 9–38)
- `current_selection()` — reads clipboard from wl/x11
- `tgwsource()` / `tgws()` — re-source both shell files + activate venv
- `tgwedit()` — backup and open tgw.source in editor

### Camera / KDE Connect controls
- `click-opencamera()`, `click-manualcamera()` — trigger phone camera shutter
- `robocam()` — robocam mode
- `nextloc()` / `setlocation()` — set current location on phone

### Whisper dictation
- `whisper()` — 15s dictation → clipboard
- `whisper-clip()` — 7s quick dictation
- `whisper-title()` — 16s title dictation with punctuation filter
- `whisper-cond()` — 20s condition description dictation
- `whisper-hint()` — continuous hint dictation loop

### Physical tools
- `get_weight()` / `weight()` / `paste_weight()` — USB scale reader

### File manager / viewer openers
- `openitem()` / `oi` — open SKU folder in Dolphin
- `openarchive()` — open item ZIP in Ark
- `openlocation()` — open location folder in Dolphin
- `searchcatalog()` — open searchcatalog.json in jsoneditor

### Browser launchers
- `research()` — Google + Amazon + eBay sold search
- `google()` / `goog` — Google search
- `eb()` — eBay search
- `ebo()` — eBay orders page
- `ebs()` — eBay sold listings search
- `ebsku()` — eBay Seller Hub listing by SKU
- `ebd()` — eBay Seller Hub listing by description
- `ebt()` — eBay Seller Hub listing by title
- `ebi()` — eBay listing by item number
- `pirateship()` — PirateShip import page

### Current item management
- `tgwset()` — set CurrentItem symlink to given SKU
- `tgwset_last_new_item()` — set CurrentItem to a new item
- `tgwset_watch_for_new_items()` — inotifywait loop for new items
- `tgwset_selected()` / `ic_current_dir` — set CurrentItem from clipboard selection
- `currentdir()` / `currentsku` — resolve CurrentItem symlink to SKU name
- `getsku()` — resolve ebay ID or partial SKU to full SKU; reads searchcatalog.json
- `tgw_sku()` — getsku wrapper

### Catalog search (direct JSON; no HTTP; fast)
- `catsearchloc()` / `catsloc` — search by location prefix
- `catsearchebaycategory()` / `catsebcat` — search by eBay category
- `catsearchstatus()` / `catstat` — search by status
- `catsearchsku()` / `catsku` — search by SKU prefix
- `catsearchtitle()` / `catstit` — search by title substring
- `catsearch()` — run all search types and concatenate
- `catsearch_result_popup()` / `catsup` — kdialog popup with search results

### Item data field readers (bash, direct JSON)
- `tgw_title()`, `tgwtitle()` — read title from item JSON
- `tgw_location()`, `tgw_loc()` — read location
- `tgw_location_box/shelf/section/row/building/complete()` — read location sub-fields
- `tgw_upc()`, `tgw_isbn()`, `tgw_brand()`, `tgw_model()`, `tgw_mpn()` — read product fields
- `tgw_price()`, `current_price()`, `ebay_price()` — read price fields
- `ebay_id()`, `ebay_qty()` — read eBay fields
- `tgw_image()`, `tgw_image_1()` — read image filenames
- `tgw_all()`, `dump()` — print entire item JSON
- `tgw_description()`, `tgw_search_terms()` — read text fields
- `tgw_info_popup()` — kdialog info dialog with full item summary

### Interactive item description
- `ebay_description()`, `ebay_desc()` — static description template with picklist line
- `ebay_gpt_desc()` — description using stored `gpt_desc` field or fallback
- `paste_ebay_desc()`, `paste_ebay_desc_html()`, `paste_gpt_desc()` — paste to clipboard
- `picklist_line()`, `picklist_question_line()`, `picklist_add()` — build picklist entries

### Backup and sync
- `tgwbackup()` — rclone sync to `/media/db/Backup`
- `tgwgdrive()` / `tgwgdrive_commands()` — rclone sync to Google Drive
- `tgwclone()` — rclone sync to `/media/db/TGW1`
- `tgwsync()` — tgwbackup + tgwclone + tgwgdrive

### Location labels
- `mkloclabel()` / `loclabel` / `mkqrloclabel` — generate QR location label PDF with glabels-3

### Utilities
- `mkclaudezip()` — zip docs/src/bin/config for Claude context upload
- `splitsku()` — duplicate a SKU folder for split-item workflow
- `increment_alphanumeric()` — helper for splitsku
- `json_backup()` / `itemdatabackup()` — backup item JSON before modifications
- `update_key_keep_old_value()` — append old value to key0/key1/... before overwrite

## tgw.source — WRAP (should call `tgw` CLI instead)

These functions write ItemData directly and should become thin CLI wrappers.
The `tgw` CLI equivalents already exist but the bash versions still directly manipulate JSON.

### Already fixed
- `mktgwcats()` → `tgw build-all` ✅ DONE

### ARCH-VIOLATES — direct JSON writes (bypass tgw-api)
These are the most critical to replace; they violate "tgw-api is the fence":

| Bash function | `tgw` CLI equivalent | Notes |
|---|---|---|
| `locationupdate()` / `locup` / `mvitem` | `tgw locationupdate <loc> <sku>` | Uses `jj` directly |
| `titleupdate()` / `titup` | `tgw titleupdate <sku> <title>` | Uses `update_key_keep_old_value` |
| `verifiedupdate()` / `verup` | `tgw verifiedupdate <sku>` | Uses `jj` directly; also writes `#VERIFIED` (pre-scrub name) |
| `statusupdate()` / `statup` | No CLI equivalent yet (TODO) | Uses `jj` directly |
| `hintupdate()` / `hintup` | `tgw hint <sku> "<hint>"` | Uses `update_key_keep_old_value` |
| `catlocmvall()` / `catmvloc` | `tgw catlocmvall <old> <new>` | Chains `catsearch` + `locationupdate` |

**Replacement pattern** (use for each function above):
```bash
locationupdate () { tgw locationupdate "$@"; }
titleupdate ()    { tgw titleupdate "$@"; }
verifiedupdate () { tgw verifiedupdate "$@"; }
```
**Note**: `verifiedupdate` also needs updating to write `verified` instead of `#VERIFIED` —
do this as part of Data Scrub Pass 1 (coordinate with `#VERIFIED` → `verified` rename).

## tgw.source — DEPRECATED (superseded, safe to remove in Tier 2 pass)

### Old catalog builders (replaced by `tgw build-*`)
These all predate the Python pipeline and were replaced by `mktgwcats` → `tgw build-all`:
- `mktgwcatalog()` — old shell-based master catalog builder using `jj`
- `mktgwcatalog-location()` — builds location symlink tree (use `tgw build-locations`)
- `mktgwcatalog-location-ebcat()` — builds eBay category symlinks
- `mktgwjson-jj()` / `mktgwjson-jj-old()` / `mktgwjson-jq()` — old JSON catalog builders
- `mktgwcsv-jj()` / `mktgwcsv-jq()` — old CSV catalog builders
- `mktgwcatalog_plus_fbimport()` — old Facebook Marketplace import builder
- `mktgwtodo()` — old todo-list catalog builder
- `mk-ebay-category-csvs()` / `mkebaycsvs` — old per-category CSV export
- `searchcatalog_versionupdate()` / `catsverup` — calls `tgw verifiedupdate` per item in location
- `jsonaddsku()` — one-time sku field fixer

### Old new-item intake workflow (replaced by Python intake workers)
These implement the original file-based intake pipeline:
- `mkjob()` / `mkjob_settings()` / `mkdraftfile()` / `mv2itemdata()` — entire old job pipeline
- `mkebaydraft()` — builds CSV eBay draft import row
- `mkebayrevise()` — builds CSV eBay revise row (with GPT-3.5 title)
- `mkebaydraftimportcsv()` / `mkebaydraftimportcsvbuilder()` / `ebaydraftimport()` — variant builders
- `data2json()` / `data2json-old()` — convert `.data` files → item JSON
- `archivenewitems()` — old archive step

### Old CSV merge pipeline (replaced by `import-sold-csv`)
- `csvmerge()` — merge any eBay export CSV into item JSONs
- `csvmerge-ebayid()` — variant that keys on eBay item ID
- `automerge()` — batch merge all Downloads CSV files
- `amtest()` / `csv2skus()` — helpers

### Old browser automation (replaced by `ebay_upload` / `ebay_draft` workers)
- `addphotos()` / `addphotos-next()` / `replacephotos()` — ydotool photo upload automation
- `resumedraft()` — xdotool eBay draft resume
- `newitem()` / `newitem-old()` — open eBay listing form
- `eb_template_default()` / `eb_template_book()` — xdotool form fill
- `paste_isbn()` / `paste_tgw_title()` / `paste_tgw_sku()` — clipboard paste + keypress

### Old KDE Connect / clipboard intake protocol
- `ic_mkitem()` — sends "COMMAND:Item Creation - Save Item" to clipboard
- `ic_data()` — sends "DATA:key=value" to clipboard
- `ic_template()` — sends "TEMPLATE:name" to clipboard
- `ic_command()` — sends "COMMAND:..." to clipboard
- `ic_test()` — syntax error artifact (line 3373 has stray content after closing brace)
- `title2isbn()` — xdotool ISBN tab sequence

### Old GPT-3.5 via sgpt (replaced by Ollama pipeline)
- `gpt_title()` — calls `sgpt` for title generation
- `gpt_desc()` / `gpt_title_old()` / `ebay_gpt_desc()` variant callers

### Old data repair / one-time fixup scripts
- `fixdatacombined()` / `restoredatacombined()` — old `data_combined.json` repair
- `fixemptytitle()` / `fixtitle()` — repair title field
- `fixemptylocation()` — repair location field
- `fixsku()` — write sku field into JSON
- `fixmess()` / `fixnodata()` — full item repair pipeline
- `clearItemnumber()` — bulk delete "Item number" field from all items
- `testfixed()` — test for `.fixed` sentinel file

### Other deprecated items
- `tgw-browser.old()` — explicit dead code
- `imagestodata()` — duplicate definition (lines 190 and 197); early Ollama experiment
- `unfoldio()` — old zip-based intake
- `tgwcd()` — one-liner around `cd`; use `oi <sku>` or `cditems` instead
- `backupitemdata()` / `archiveitemdatatmp()` / `archiveitemzips()` — old item archive routines
- `set_queue()` — old symlink-based queue mechanism

## Tier 2 removal plan (do in a future session)

1. Remove all DEPRECATED blocks above (grep for each function name first to confirm no callers)
2. Replace ARCH-VIOLATES functions with thin `tgw` CLI wrappers (coordinate with Data Scrub Pass 1)
3. After `#VERIFIED` → `verified` rename: update `verifiedupdate()` wrapper
4. Remove `_tgw_py()` and `_tgw_bin()` from `tgw-dev.source` (only used by old `tgw-rebuild`)
5. Merge remaining useful `tgw-dev.source` helpers into `tgw.source` or leave as-is
6. Fix `ic_test()` syntax error (line 3373 — stray content after closing brace)

## pyproject.toml console scripts note

The `tgw` package already exposes its entry points via pyproject.toml console scripts.
No bash functions need promotion — the `tgw` CLI is the right mechanism for all non-interactive
commands. Keep bash for interactive/session helpers (camera, whisper, browser launchers,
clipboard paste, Dolphin openers).
