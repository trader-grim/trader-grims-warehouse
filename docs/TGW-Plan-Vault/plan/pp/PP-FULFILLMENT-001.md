## PP-FULFILLMENT-001 — Fulfillment Hardware Integration

### Problem
Shipping, labeling, and order packing still require manual steps outside TGW. USB scale and
barcode printing are natural integration points that reduce fulfillment time.

### Components

#### USB Scale
- `weight()` / `get_weight()` already in `tgw.source` — reads USB HID device
- Port to Python: `hid` library or `/dev/usb/hiddev` direct read
- Use at intake (size_class derivation) + at shipping (label weight verification)
- See PERPLEXITY-005 for USB HID library options

#### Barcode / SKU label printing
- Target: thermal printer (Dymo 4XL or Zebra ZPL) connected via USB
- SKU barcode label: Code128 + human-readable SKU + item title + location
- CLI: `tgw print-label <sku>` — generates and sends to printer
- Library: `python-barcode` (Code128) + `cups` or direct ZPL for Zebra

#### Shipping label printing
- eBay shipping labels via eBay Shipping API or browser fallback (Seller Hub)
- `tgw print-shipping <order_id>` — fetches label PDF from eBay API, sends to printer
- Requires `sell.fulfillment.readonly` scope (in desired scope list — not yet approved)

#### Packing list
- ⚠️ **CORRECTION (session 15 audit)**: `tgw picklist` does **NOT** exist yet — only
  `picklist_line()` in `ebay/description.py` (one line per eBay description). Track 1 round-2
  rank 7 builds the real location-sorted `tgw picklist` CLI; this print action extends it.
- Print-ready PDF: location-sorted, grouped by order, checkboxes per item
- QR code on packing list: encodes SKU or order ID for scan-to-confirm

### Dependencies
- USB scale: HID library (PERPLEXITY-005 research covers this)
- Label printing: thermal printer hardware (operator purchase)
- Shipping labels: `sell.fulfillment.readonly` eBay scope
- PDF generation: `reportlab` or `weasyprint`

---

