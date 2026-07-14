"""
tgw.ebay.description — Listing description builder.

Combines the AI-generated item description with the seller boilerplate
footer and the picklist line. The picklist line is machine-parseable
and used for warehouse picking and future QR code generation.

Picklist line format (matches tgw.source convention):
  tgw-pl::=::{location}::=::"{title}"::=:{sku}::=::"{ebay_id}"

The QR code evolution path: when cfg['description_picklist_qr'] is True,
a QR code image (pre-uploaded to eBay EPS) is embedded instead of the
plain text line.  Plain text remains as fallback.

Public API:
    build_listing_description(item, cfg) → HTML string
    picklist_line(item) → plain text line
"""

from __future__ import annotations

import html as _html
from typing import Any, Dict

_DEFAULT_BOILERPLATE = (
    "Please see our photos for more information about the item. "
    "If you need more information about the item please send a message. "
    "Photos are of the exact item you are receiving with the occasional "
    "exception of items that have multiple quantity. Please make sure "
    "you take a look before you buy."
    "\n\n"
    "We ship quickly and securely with same or next day handling wherever "
    "possible. If you have questions about the item or special packaging "
    "or shipping requirements please contact us before placing your order."
)


def picklist_line(item: Dict[str, Any]) -> str:
    """
    Return the picklist line for warehouse picking and Google Sheet sync.

    Format (matches tgw.source / phone app):
      tgw-pl::=::{location}:=:{title}:=:{sku}:=:{ebay_id|null}

    First separator is ::=:: (double), rest are :=: (single).
    No quotes. ebay_id is 'null' until published.
    """
    sku      = item.get('sku', '')
    location = item.get('location', '')
    title    = (item.get('title', '')
                or item.get('draft_listing', {}).get('title', ''))
    ebay_id  = item.get('ebay_listing', {}).get('listing_id') or 'null'
    return f'tgw-pl::=::{location}:=:{title}:=:{sku}:=:{ebay_id}'


def build_listing_description(item: Dict[str, Any],
                               cfg: Dict[str, Any]) -> str:
    """
    Build the full eBay listing description HTML.

    Structure:
      <p>{AI-generated description}</p>
      <p>{boilerplate line 1}</p>
      <p>{boilerplate line 2}</p>
      <p>{picklist line}</p>
    """
    draft       = item.get('draft_listing', {})
    ai_desc     = (draft.get('description')
                   or item.get('description', '')).strip()
    boilerplate = cfg.get('description_footer', _DEFAULT_BOILERPLATE).strip()

    # Boilerplate may have two paragraphs separated by double newline
    bp_paras = [p.strip() for p in boilerplate.split('\n\n') if p.strip()]
    bp_html  = ''.join(f'<p>{p}</p>' for p in bp_paras)

    pl = picklist_line(item)

    return f'<p>{_html.escape(ai_desc)}</p>{bp_html}<p>{_html.escape(pl)}</p>'
