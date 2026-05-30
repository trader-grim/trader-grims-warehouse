# TGW Tools

Maintenance and utility scripts. Not part of the installed package.
Run directly with Python from the repo root.

## repair_itemdata_json.py
Scans ItemData and repairs malformed JSON files.
```bash
python tools/repair_itemdata_json.py [--root /opt/TGW/data/ItemData]
```

## cliptitleup.py
Clipboard utility — strips punctuation and title-cases whatever is on
the clipboard. Useful at the barcode station.
```bash
python tools/cliptitleup.py
```

## migrate_batch.py
eBay bulk migration script — migrates legacy eBay listings to the
Inventory API format. Used when importing existing eBay listings.
Requires EBAY_OAUTH_TOKEN environment variable.
```bash
EBAY_OAUTH_TOKEN=<token> python tools/migrate_batch.py
```
