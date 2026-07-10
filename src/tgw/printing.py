"""
tgw.printing — PDF generation for picklist and SKU labels (PP-ADD-009 / PP-FULFILLMENT-001 Phase 1).

CUPS printing is stubbed: set config key ``print_cups_queue`` to a printer name
to auto-send after PDF generation.  Printer hardware is not yet wired up; the key
is intentionally absent from the default config so the feature is inert until the
hardware arrives.

Requires optional-dep group 'printing':  pip install 'trader-grims-warehouse[printing]'
  reportlab>=4.0    — PDF layout + built-in Code128 barcode
  qrcode[pil]>=7.4  — QR code generation (uses Pillow for PNG output)
"""

from __future__ import annotations

import io
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def build_picklist_pdf(
    rows: List[Dict[str, Any]],
    output_path,
    *,
    title: str = "Pick List",
) -> Path:
    """Generate a location-sorted picklist PDF with checkboxes and per-row QR codes.

    rows: list of {location, sku, title, ebay_id}
    output_path: Path or str — file is created (parent dirs made as needed)
    Returns: resolved Path to the written file
    """
    import qrcode as qrcode_lib
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas as rl_canvas

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    PAGE_W, PAGE_H = letter
    MARGIN = 0.5 * inch
    ROW_H = 0.42 * inch
    QR_SIZE = 0.36 * inch
    HEADER_H = 0.55 * inch

    c = rl_canvas.Canvas(str(output_path), pagesize=letter)
    now_str = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")

    def _draw_page_header():
        c.setFont("Helvetica-Bold", 14)
        c.setFillColor(colors.black)
        c.drawString(MARGIN, PAGE_H - MARGIN, f"{title}  —  {now_str}")
        c.setLineWidth(0.5)
        c.line(MARGIN, PAGE_H - MARGIN - 4, PAGE_W - MARGIN, PAGE_H - MARGIN - 4)

    _draw_page_header()
    y = PAGE_H - MARGIN - HEADER_H
    current_loc: Optional[str] = None

    for row in rows:
        loc = row.get("location") or "(unlocated)"

        if loc != current_loc:
            # New location section header
            if y < MARGIN + ROW_H * 2:
                c.showPage()
                _draw_page_header()
                y = PAGE_H - MARGIN - HEADER_H
            y -= 4
            c.setFont("Helvetica-Bold", 10)
            c.setFillColor(colors.HexColor("#2a4a7f"))
            c.drawString(MARGIN, y, f"  {loc}")
            c.setFillColor(colors.black)
            y -= 3
            c.setLineWidth(0.4)
            c.line(MARGIN, y, PAGE_W - MARGIN, y)
            y -= 4
            current_loc = loc

        if y < MARGIN + ROW_H:
            c.showPage()
            _draw_page_header()
            y = PAGE_H - MARGIN - HEADER_H

        row_bottom = y - ROW_H

        # Checkbox (10 × 10 pt box)
        box_x = MARGIN + 2
        box_y = y - ROW_H * 0.62
        c.setLineWidth(0.9)
        c.rect(box_x, box_y, 10, 10)

        # SKU (monospace) + title
        text_x = MARGIN + 18
        sku = row.get("sku", "")
        item_title = (row.get("title") or "")[:58]
        ebay_part = f"  [{row['ebay_id']}]" if row.get("ebay_id") else ""

        c.setFont("Courier-Bold", 8)
        c.setFillColor(colors.black)
        c.drawString(text_x, y - ROW_H * 0.30, sku)
        c.setFont("Helvetica", 7)
        c.drawString(text_x, y - ROW_H * 0.62, item_title + ebay_part)

        # QR code (encodes SKU) on the right
        qr_x = PAGE_W - MARGIN - QR_SIZE - 4
        qr_y = row_bottom + (ROW_H - QR_SIZE) / 2
        try:
            qr = qrcode_lib.QRCode(version=1, box_size=3, border=1,
                                    error_correction=qrcode_lib.constants.ERROR_CORRECT_L)
            qr.add_data(sku)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            qr_img.save(buf, format="PNG")
            buf.seek(0)
            c.drawImage(buf, qr_x, qr_y, QR_SIZE, QR_SIZE)
        except Exception:
            pass  # QR failure is non-fatal

        # Row separator (light)
        c.setLineWidth(0.2)
        c.setStrokeColor(colors.HexColor("#cccccc"))
        c.line(MARGIN + 18, row_bottom, PAGE_W - MARGIN, row_bottom)
        c.setStrokeColor(colors.black)

        y -= ROW_H

    c.save()
    return output_path


def build_label_pdf(
    sku: str,
    item_title: str,
    location: str,
    output_path,
) -> Path:
    """Generate a 2.25" × 1.25" Code128 SKU label PDF (Dymo / ZPL target size).

    Uses reportlab's built-in Code128 barcode — no extra barcode library needed.
    """
    from reportlab.graphics.barcode.code128 import Code128
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas as rl_canvas

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    W = 2.25 * inch
    H = 1.25 * inch

    c = rl_canvas.Canvas(str(output_path), pagesize=(W, H))

    # Code128 barcode centred in the top ~half of the label
    barcode = Code128(sku, barWidth=0.9, barHeight=0.40 * inch, humanReadable=False)
    bc_w = barcode.width
    bc_x = max(0.0, (W - bc_w) / 2)
    barcode.drawOn(c, bc_x, H - 0.48 * inch)

    # SKU text
    c.setFont("Courier-Bold", 6)
    c.drawCentredString(W / 2, H - 0.58 * inch, sku)

    # Title (truncated to fit)
    c.setFont("Helvetica", 6)
    c.drawCentredString(W / 2, H - 0.73 * inch, (item_title or "")[:38])

    # Location badge at bottom
    if location:
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(W / 2, 0.08 * inch, f"Loc: {location}")

    c.save()
    return output_path


def cups_print(path, queue: str) -> bool:
    """Send a PDF file to a CUPS printer queue.

    Stub until printer hardware is wired — returns False (not an error) when
    lpr is unavailable.  Set ``print_cups_queue`` in config to activate.
    """
    try:
        result = subprocess.run(
            ["lpr", "-P", queue, str(path)],
            capture_output=True, text=True, timeout=15, check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ---------------------------------------------------------------------------
# Default output path helpers
# ---------------------------------------------------------------------------


def _default_picklist_path() -> Path:
    ts = datetime.now().astimezone().strftime("%Y%m%d%H%M%S")
    return Path(tempfile.gettempdir()) / f"tgw-picklist-{ts}.pdf"


def _default_label_path(sku: str) -> Path:
    return Path(tempfile.gettempdir()) / f"tgw-label-{sku}.pdf"
