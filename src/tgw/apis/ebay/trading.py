"""
tgw.apis.ebay.trading — eBay Trading API client (XML/IAF-token).

Used for operations not available in the REST Inventory API:
  - GetMyeBaySelling: all active listings regardless of how they were created
  - revise_item_sku: update the custom label (SKU) on a live listing in place

Auth: same OAuth access token as the REST API, passed via X-EBAY-API-IAF-TOKEN.
Response: XML parsed with ElementTree.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, Generator

import requests

from tgw.apis.ebay.client import load_token

log = logging.getLogger(__name__)

_TRADING_ENDPOINT = 'https://api.ebay.com/ws/api.dll'
_API_VERSION      = '1155'
_SITE_ID          = '0'       # EBAY_US
_NS               = 'urn:ebay:apis:eBLBaseComponents'
_SESSION          = requests.Session()


def _t(tag: str) -> str:
    return f'{{{_NS}}}{tag}'


def trading_call(cfg: Dict[str, Any], call_name: str,
                 xml_body: str, timeout: int = 60) -> ET.Element:
    """
    Make a Trading API call.  Returns the parsed XML root element.
    Raises RuntimeError if eBay returns Ack=Failure.
    """
    token = load_token(cfg)
    headers = {
        'X-EBAY-API-IAF-TOKEN':          token,
        'X-EBAY-API-COMPATIBILITY-LEVEL': _API_VERSION,
        'X-EBAY-API-CALL-NAME':          call_name,
        'X-EBAY-API-SITEID':             _SITE_ID,
        'Content-Type':                  'text/xml;charset=utf-8',
    }
    resp = _SESSION.post(_TRADING_ENDPOINT, headers=headers,
                         data=xml_body.encode('utf-8'), timeout=timeout)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    ack  = root.findtext(_t('Ack')) or ''
    if ack == 'Failure':
        errs = root.findall(f'.//{_t("LongMessage")}')
        msg  = '; '.join(e.text or '' for e in errs) or 'unknown error'
        raise RuntimeError(f'Trading API {call_name} failed: {msg}')
    return root


def _order_from_xml(order_el: ET.Element) -> Dict[str, Any]:
    """Extract order data (order_id, buyer, transactions) from an <Order> element."""
    def txt(tag: str) -> str:
        el = order_el.find(_t(tag))
        return (el.text or '').strip() if el is not None else ''

    transactions = []
    tx_array = order_el.find(_t('TransactionArray'))
    if tx_array is not None:
        for tx in tx_array.findall(_t('Transaction')):
            item_el = tx.find(_t('Item'))
            listing_id = ''
            if item_el is not None:
                el = item_el.find(_t('ItemID'))
                listing_id = (el.text or '').strip() if el is not None else ''

            price_el = tx.find(_t('TransactionPrice'))
            price = float(price_el.text) if price_el is not None and price_el.text else None

            qty_el = tx.find(_t('QuantityPurchased'))
            qty = int(qty_el.text or '1') if qty_el is not None else 1

            date_el = tx.find(_t('CreatedDate'))
            sale_date = (date_el.text or '').strip() if date_el is not None else txt('CreatedTime')

            transactions.append({
                'listing_id': listing_id,
                'sale_price': price,
                'quantity':   qty,
                'sale_date':  sale_date,
            })

    return {
        'order_id':    txt('OrderID'),
        'buyer':       txt('BuyerUserID'),
        'created_at':  txt('CreatedTime'),
        'transactions': transactions,
    }


def get_orders(cfg: Dict[str, Any],
               create_time_from: 'datetime',
               create_time_to:   'datetime') -> 'Generator[Dict[str, Any], None, None]':
    """
    Yield completed orders in the given date window (max 90 days per call).
    Handles pagination automatically.
    """
    page = 1
    total_pages = 1

    while page <= total_pages:
        fmt = '%Y-%m-%dT%H:%M:%S.000Z'
        xml_body = f'''<?xml version="1.0" encoding="utf-8"?>
<GetOrdersRequest xmlns="{_NS}">
  <CreateTimeFrom>{create_time_from.strftime(fmt)}</CreateTimeFrom>
  <CreateTimeTo>{create_time_to.strftime(fmt)}</CreateTimeTo>
  <OrderStatus>Completed</OrderStatus>
  <DetailLevel>ReturnAll</DetailLevel>
  <Pagination>
    <EntriesPerPage>100</EntriesPerPage>
    <PageNumber>{page}</PageNumber>
  </Pagination>
</GetOrdersRequest>'''

        root = trading_call(cfg, 'GetOrders', xml_body, timeout=120)

        pagination = root.find(_t('PaginationResult'))
        if pagination is not None:
            total_pages = int(pagination.findtext(_t('TotalNumberOfPages')) or '1')

        order_array = root.find(_t('OrderArray'))
        if order_array is None:
            return

        for order_el in order_array.findall(_t('Order')):
            yield _order_from_xml(order_el)

        page += 1


def _item_from_xml(item_el: ET.Element) -> Dict[str, Any]:
    """Extract a flat dict of useful fields from a <Item> element."""
    def txt(tag: str) -> str:
        el = item_el.find(_t(tag))
        return (el.text or '').strip() if el is not None else ''

    selling = item_el.find(_t('SellingStatus'))
    price_el = selling.find(_t('CurrentPrice')) if selling is not None else None
    price = float(price_el.text) if price_el is not None and price_el.text else None

    details = item_el.find(_t('ListingDetails'))
    url = ''
    if details is not None:
        url_el = details.find(_t('ViewItemURL'))
        url = (url_el.text or '').strip() if url_el is not None else ''

    # eBay uses either <SKU> or <CustomLabel> for the seller's custom label;
    # tgw.source used <SKU>; browser/Seller Hub listings may use <CustomLabel>
    custom_label = txt('SKU') or txt('CustomLabel')

    return {
        'listing_id':   txt('ItemID'),
        'title':        txt('Title'),
        'custom_label': custom_label,         # = TGW SKU
        'status':       txt('ListingStatus') or (
                            selling.findtext(_t('ListingStatus')) or 'Active'
                            if selling is not None else 'Active'),
        'live_price':   price,
        'listing_url':  url,
        'quantity':     int(txt('Quantity') or '1'),
        'quantity_sold': int(txt('QuantitySold') or '0'),
    }


def get_my_ebay_selling(cfg: Dict[str, Any],
                        page_size: int = 200) -> Generator[Dict[str, Any], None, None]:
    """
    Yield all active Trading API listings for the authenticated seller.
    Handles pagination automatically.
    """
    page = 1
    total_pages = 1

    while page <= total_pages:
        xml_body = f'''<?xml version="1.0" encoding="utf-8"?>
<GetMyeBaySellingRequest xmlns="{_NS}">
  <ActiveList>
    <Include>true</Include>
    <Pagination>
      <EntriesPerPage>{page_size}</EntriesPerPage>
      <PageNumber>{page}</PageNumber>
    </Pagination>
    <Sort>ItemID</Sort>
  </ActiveList>
  <DetailLevel>ReturnAll</DetailLevel>
</GetMyeBaySellingRequest>'''

        root = trading_call(cfg, 'GetMyeBaySelling', xml_body, timeout=90)

        active_list = root.find(_t('ActiveList'))
        if active_list is None:
            log.info('GetMyeBaySelling: no ActiveList in response (no active listings)')
            return

        # Pagination info
        pagination = active_list.find(_t('PaginationResult'))
        if pagination is not None:
            total_pages = int(pagination.findtext(_t('TotalNumberOfPages')) or '1')

        item_array = active_list.find(_t('ItemArray'))
        if item_array is None:
            return

        items = item_array.findall(_t('Item'))
        log.info('GetMyeBaySelling page %d/%d: %d items', page, total_pages, len(items))

        for item_el in items:
            yield _item_from_xml(item_el)

        page += 1


def revise_item_sku(cfg: Dict[str, Any], listing_id: str, new_sku: str) -> None:
    """
    Change the custom label (SKU field) on a live Trading API listing in-place.

    Uses ReviseFixedPriceItem with only ItemID + SKU — every other field is
    left untouched.  Listing age, watchers, listing_id, and price are preserved.
    """
    xml_body = f'''<?xml version="1.0" encoding="utf-8"?>
<ReviseFixedPriceItemRequest xmlns="{_NS}">
  <Item>
    <ItemID>{listing_id}</ItemID>
    <SKU>{new_sku}</SKU>
  </Item>
</ReviseFixedPriceItemRequest>'''
    trading_call(cfg, 'ReviseFixedPriceItem', xml_body)
    log.info('ReviseFixedPriceItem: listing %s custom label → %s', listing_id, new_sku)
