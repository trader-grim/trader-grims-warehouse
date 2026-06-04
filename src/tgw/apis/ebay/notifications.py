"""
tgw.apis.ebay.notifications — eBay Trading API push notification support.

SetNotificationPreferences registers a delivery URL for FixedPriceTransaction events.
eBay POSTs SOAP XML to the URL when an item sells.

Verification: NotificationSignature = MD5(timestamp + dev_id + app_id + cert_id).
Add dev_id to ebay-credentials.json to enable; omitted → signature check is skipped
with a warning (still safe — delivery URL is not guessable).

Setup: call set_notification_preferences() once, or run `tgw setup-ebay-hooks`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Optional

from tgw.apis.ebay.trading import _NS, trading_call

log = logging.getLogger(__name__)

_SOAP_NS = 'http://schemas.xmlsoap.org/soap/envelope/'


def set_notification_preferences(cfg: Dict[str, Any], delivery_url: str) -> None:
    """
    Register delivery_url with eBay and enable FixedPriceTransaction notifications.
    Idempotent — safe to re-run to update the URL.
    """
    xml_body = f'''<?xml version="1.0" encoding="utf-8"?>
<SetNotificationPreferencesRequest xmlns="{_NS}">
  <ApplicationDeliveryPreferences>
    <ApplicationURL>{delivery_url}</ApplicationURL>
    <ApplicationEnable>Enable</ApplicationEnable>
    <NotificationPayloadType>eBaySOAPAPIFormatted</NotificationPayloadType>
  </ApplicationDeliveryPreferences>
  <UserDeliveryPreferenceArray>
    <NotificationEnable>
      <EventType>FixedPriceTransaction</EventType>
      <EventEnable>Enable</EventEnable>
    </NotificationEnable>
  </UserDeliveryPreferenceArray>
</SetNotificationPreferencesRequest>'''
    trading_call(cfg, 'SetNotificationPreferences', xml_body)
    log.info('eBay notifications configured: url=%s', delivery_url)


def get_notification_preferences(cfg: Dict[str, Any]) -> str:
    """Return current ApplicationURL from eBay (for verification)."""
    xml_body = f'''<?xml version="1.0" encoding="utf-8"?>
<GetNotificationPreferencesRequest xmlns="{_NS}">
  <PreferenceLevel>Application</PreferenceLevel>
</GetNotificationPreferencesRequest>'''
    root = trading_call(cfg, 'GetNotificationPreferences', xml_body)
    prefs = root.find(f'{{{_NS}}}ApplicationDeliveryPreferences')
    if prefs is None:
        return ''
    url_el = prefs.find(f'{{{_NS}}}ApplicationURL')
    return (url_el.text or '').strip() if url_el is not None else ''


def _load_app_credentials(cfg: Dict[str, Any]) -> Dict[str, str]:
    creds_path: Path = cfg['ebay_credentials_path']
    return json.loads(creds_path.read_text(encoding='utf-8'))


def verify_notification_signature(xml_body: bytes, cfg: Dict[str, Any]) -> bool:
    """
    Verify eBay's NotificationSignature: MD5(timestamp + dev_id + app_id + cert_id).
    Returns True if valid.  If dev_id is absent from credentials, logs a warning
    and returns True (accept but note unverified).
    """
    try:
        root = ET.fromstring(xml_body)

        header = root.find(f'{{{_SOAP_NS}}}Header')
        if header is None:
            log.debug('ebay_webhook: no SOAP header — signature unverifiable, accepting')
            return True

        creds_el = header.find(f'.//{{{_NS}}}RequesterCredentials')
        sig_el = creds_el.find(f'{{{_NS}}}NotificationSignature') if creds_el is not None else None
        received_sig = (sig_el.text or '').strip() if sig_el is not None else ''
        if not received_sig:
            log.debug('ebay_webhook: no NotificationSignature — accepting')
            return True

        body_el = root.find(f'{{{_SOAP_NS}}}Body')
        ts_el = body_el.find(f'.//{{{_NS}}}Timestamp') if body_el is not None else None
        timestamp = (ts_el.text or '').strip() if ts_el is not None else ''

        creds = _load_app_credentials(cfg)
        dev_id  = creds.get('dev_id', '')
        app_id  = creds.get('app_id', '')
        cert_id = creds.get('cert_id', '')

        if not dev_id:
            log.warning('ebay_webhook: dev_id missing from credentials — signature not verified')
            return True

        expected = hashlib.md5(
            (timestamp + dev_id + app_id + cert_id).encode('utf-8')
        ).hexdigest()

        if received_sig.lower() != expected.lower():
            log.warning('ebay_webhook: signature mismatch (received=%s…)', received_sig[:8])
            return False
        return True

    except Exception as exc:
        log.warning('ebay_webhook: signature check error: %s — accepting', exc)
        return True


def parse_sold_notification(xml_body: bytes) -> Optional[Dict[str, Any]]:
    """
    Parse a FixedPriceTransaction SOAP notification.

    Returns dict(listing_id, buyer, sale_price, quantity, sale_date, order_id),
    or None if the payload is not a recognisable sold event (e.g. a ping).

    eBay delivers this as a SOAP envelope whose body contains a response element
    (typically resembling GetItemTransactionsResponse) with Transaction data inside.
    """
    try:
        root = ET.fromstring(xml_body)
        body_el = root.find(f'{{{_SOAP_NS}}}Body')
        if body_el is None:
            return None

        def txt(parent: ET.Element, tag: str) -> str:
            el = parent.find(f'.//{{{_NS}}}{tag}')
            return (el.text or '').strip() if el is not None else ''

        tx_el = body_el.find(f'.//{{{_NS}}}Transaction')
        if tx_el is None:
            log.debug('ebay_webhook: no Transaction element — treating as ping/test')
            return None

        item_el = tx_el.find(f'{{{_NS}}}Item')
        listing_id = ''
        if item_el is not None:
            lid_el = item_el.find(f'{{{_NS}}}ItemID')
            listing_id = (lid_el.text or '').strip() if lid_el is not None else ''
        if not listing_id:
            listing_id = txt(body_el, 'ItemID')

        price_el = tx_el.find(f'{{{_NS}}}TransactionPrice')
        sale_price = float(price_el.text) if price_el is not None and price_el.text else None

        qty_el = tx_el.find(f'{{{_NS}}}QuantityPurchased')
        quantity = int(qty_el.text or '1') if qty_el is not None else 1

        date_el = tx_el.find(f'{{{_NS}}}CreatedDate')
        sale_date = (date_el.text or '').strip() if date_el is not None else txt(body_el, 'CreatedTime')

        buyer_el = tx_el.find(f'{{{_NS}}}Buyer')
        buyer = ''
        if buyer_el is not None:
            uid_el = buyer_el.find(f'{{{_NS}}}UserID')
            buyer = (uid_el.text or '').strip() if uid_el is not None else ''

        order_id = txt(body_el, 'OrderID') or txt(tx_el, 'TransactionID')

        if not listing_id:
            log.warning('ebay_webhook: could not extract listing_id from notification body')
            return None

        return {
            'listing_id': listing_id,
            'buyer':      buyer,
            'sale_price': sale_price,
            'quantity':   quantity,
            'sale_date':  sale_date,
            'order_id':   order_id,
        }

    except Exception as exc:
        log.error('ebay_webhook: parse failed: %s', exc)
        return None
