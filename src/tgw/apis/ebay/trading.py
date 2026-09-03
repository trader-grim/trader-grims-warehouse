"""
tgw.apis.ebay.trading — eBay Trading API client (XML/IAF-token).

Used for operations not available in the REST Inventory API:
  - GetMyeBaySelling: all active listings regardless of how they were created
  - EndFixedPriceItem: withdraw a legacy Trading listing after governed admission
  - revise_item_sku: update the custom label (SKU) on a live listing in place
  - get_best_offers: poll incoming Best Offer requests
  - respond_to_best_offer: Accept / Decline / Counter a Best Offer

Auth: same OAuth access token as the REST API, passed via X-EBAY-API-IAF-TOKEN.
Response: XML parsed with ElementTree.
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional
from xml.sax.saxutils import escape as _xml_escape

import requests

from tgw import quota
from tgw.apis.ebay.client import _environment, capture_response, load_token
from tgw.config import ebay_environment_settings

log = logging.getLogger(__name__)

_TRADING_ENDPOINT = 'https://api.ebay.com/ws/api.dll'
_API_VERSION      = '1155'
_SITE_ID          = '0'       # EBAY_US
_NS               = 'urn:ebay:apis:eBLBaseComponents'
_SESSION          = requests.Session()


def _trading_endpoint(cfg: Dict[str, Any]) -> str:
    # Like REST, Trading accepts only the closed production/sandbox selector.
    # Arbitrary provider endpoints in caller configuration are ignored.
    return ebay_environment_settings(_environment(cfg))['trading_api_endpoint']

# eBay Motors is a distinct Trading API SiteID from EBAY_US, even though (per
# PP-EBAY-MOTORS-001 scoping) it shares the EBAY_US category tree. Public
# wrappers below accept the same marketplace_id string already used
# elsewhere in the codebase (item['marketplace_id'], the Inventory API's
# marketplaceId) so callers never need to know Trading API's separate SiteID
# scheme (todo #1214 follow-up).
_MARKETPLACE_TO_SITE_ID: Dict[str, str] = {
    'EBAY_US':     '0',
    'EBAY_MOTORS': '100',
}


def _resolve_site_id(marketplace_id: Optional[str]) -> str:
    """Translate a marketplace_id (or None) to a Trading API SiteID,
    defaulting to EBAY_US for unset/unknown values."""
    if not marketplace_id:
        return _SITE_ID
    return _MARKETPLACE_TO_SITE_ID.get(marketplace_id, _SITE_ID)


def _t(tag: str) -> str:
    return f'{{{_NS}}}{tag}'


def trading_call(cfg: Dict[str, Any], call_name: str,
                 xml_body: str, timeout: int = 60,
                 site_id: str = _SITE_ID) -> ET.Element:
    """
    Make a Trading API call.  Returns the parsed XML root element.
    Raises RuntimeError if eBay returns Ack=Failure.

    *site_id* defaults to EBAY_US ('0'); pass '100' (EBAY_MOTORS) for
    listings on that marketplace (PP-EBAY-MOTORS-001, todo #1214 follow-up
    — every Trading API call used to hardcode SiteID=0 regardless of which
    marketplace the target listing actually lives on).
    """
    token = load_token(cfg)
    headers = {
        'X-EBAY-API-IAF-TOKEN':          token,
        'X-EBAY-API-COMPATIBILITY-LEVEL': _API_VERSION,
        'X-EBAY-API-CALL-NAME':          call_name,
        'X-EBAY-API-SITEID':             site_id,
        'Content-Type':                  'text/xml;charset=utf-8',
    }
    quota.precheck(cfg, 'ebay_trading')
    resp = _SESSION.post(_trading_endpoint(cfg), headers=headers,
                         data=xml_body.encode('utf-8'), timeout=timeout)
    quota.record(cfg, 'ebay_trading')
    if resp.status_code == 429:
        quota.record_429(cfg, 'ebay_trading', call_name)
    capture_response(cfg, 'trading', call_name, None, resp.status_code, resp.content)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    ack  = root.findtext(_t('Ack')) or ''
    if ack == 'Failure':
        errs = root.findall(f'.//{_t("LongMessage")}')
        msg  = '; '.join(e.text or '' for e in errs) or 'unknown error'
        # Trading reports quota exhaustion as Ack=Failure, not HTTP 429
        if 'usage limit' in msg.lower():
            quota.record_429(cfg, 'ebay_trading', f'{call_name}: {msg}')
        raise RuntimeError(f'Trading API {call_name} failed: {msg}')
    return root


_RETRY_DELAYS = [1, 4, 16]


def _trading_call_retrying(cfg: Dict[str, Any], call_name: str, xml_body: str,
                           timeout: int, site_id: str) -> ET.Element:
    """trading_call() wrapped with 429/call-limit retry+backoff.

    Every Trading API call shares the same trading_call() choke point, but
    only get_best_offers() had this retry logic — get_orders() and
    get_my_ebay_selling() would raise on the first rate limit (audit#1143).
    """
    _last_exc: Optional[Exception] = None
    for _attempt, _delay in enumerate([0] + _RETRY_DELAYS):
        if _delay:
            time.sleep(_delay)
        try:
            root = trading_call(cfg, call_name, xml_body, timeout=timeout, site_id=site_id)
            return root
        except Exception as exc:
            _raw = str(exc)
            # 429 or eBay call-limit error code 21919188
            if '429' in _raw or '21919188' in _raw:
                log.warning('%s: rate limited (attempt %d): %s', call_name, _attempt + 1, exc)
                _last_exc = exc
                if _attempt < len(_RETRY_DELAYS):
                    continue
            raise
    raise _last_exc  # type: ignore[misc]


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
               create_time_to:   'datetime',
               marketplace_id: Optional[str] = None) -> 'Generator[Dict[str, Any], None, None]':
    """
    Yield completed orders in the given date window (max 90 days per call).
    Handles pagination automatically.

    *marketplace_id* (e.g. 'EBAY_MOTORS') selects the Trading API SiteID;
    defaults to EBAY_US. This call is per-site — it does NOT automatically
    cover every marketplace an account sells on (todo #1214 follow-up: full
    multi-site coverage for fleet-wide calls is a separate, not-yet-scoped
    piece of PP-EBAY-MOTORS-001).
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

        root = _trading_call_retrying(cfg, 'GetOrders', xml_body, timeout=120,
                                      site_id=_resolve_site_id(marketplace_id))

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


def end_item(
    cfg: Dict[str, Any],
    listing_id: str,
    reason: str = "NotAvailable",
    marketplace_id: Optional[str] = None,
) -> None:
    """End one legacy listing after the caller's governed effect admission.

    This provider primitive carries no ambient operator authority of its own;
    the only production caller is the operator-object withdrawal effect, which
    reserves the exact operation before invoking it.
    """
    xml_body = (
        f'<?xml version="1.0" encoding="utf-8"?>\n'
        f'<EndFixedPriceItemRequest xmlns="{_NS}">\n'
        f'  <ItemID>{_xml_escape(listing_id)}</ItemID>\n'
        f'  <EndingReason>{_xml_escape(reason)}</EndingReason>\n'
        f'</EndFixedPriceItemRequest>'
    )
    trading_call(
        cfg,
        "EndFixedPriceItem",
        xml_body,
        timeout=30,
        site_id=_resolve_site_id(marketplace_id),
    )
    log.info("EndFixedPriceItem: listing %s ended (reason=%s)", listing_id, reason)


def get_my_ebay_selling(cfg: Dict[str, Any],
                        page_size: int = 200,
                        marketplace_id: Optional[str] = None) -> Generator[Dict[str, Any], None, None]:
    """
    Yield all active Trading API listings for the authenticated seller.
    Handles pagination automatically.

    *marketplace_id* (e.g. 'EBAY_MOTORS') selects the Trading API SiteID;
    defaults to EBAY_US. This call is per-site — a caller wanting full
    fleet coverage across marketplaces must call it once per known
    marketplace_id and merge results (todo #1214 follow-up; not done
    automatically here).
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

        root = _trading_call_retrying(cfg, 'GetMyeBaySelling', xml_body, timeout=90,
                                      site_id=_resolve_site_id(marketplace_id))

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


def get_store_categories(cfg: Dict[str, Any],
                         marketplace_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Return flat list of eBay store custom categories: [{id, name, path}].
    Parses up to 3 levels of nesting from GetStore.
    Returns [] if the seller has no store or no custom categories configured.

    *marketplace_id* (e.g. 'EBAY_MOTORS') selects the Trading API SiteID;
    defaults to EBAY_US.
    """
    xml_body = f'''<?xml version="1.0" encoding="utf-8"?>
<GetStoreRequest xmlns="{_NS}">
  <LevelLimit>3</LevelLimit>
</GetStoreRequest>'''

    try:
        root = trading_call(cfg, 'GetStore', xml_body, timeout=30,
                            site_id=_resolve_site_id(marketplace_id))
    except RuntimeError:
        return []

    store = root.find(_t('Store'))
    if store is None:
        return []

    cat_array = store.find(_t('CustomCategories'))
    if cat_array is None:
        return []

    def _parse(parent_el: 'ET.Element', parent_path: str = '') -> 'List[Dict[str, Any]]':
        result = []
        for cat in parent_el.findall(_t('CustomCategory')):
            cid  = (cat.findtext(_t('CategoryID')) or '').strip()
            name = (cat.findtext(_t('Name'))       or '').strip()
            path = f'{parent_path} > {name}' if parent_path else name
            if cid and name:
                result.append({'id': cid, 'name': name, 'path': path})
            result.extend(_parse(cat, path))
        return result

    return _parse(cat_array)


def revise_item_sku(cfg: Dict[str, Any], listing_id: str, new_sku: str,
                    marketplace_id: Optional[str] = None) -> None:
    """
    Change the custom label (SKU field) on a live Trading API listing in-place.

    Uses ReviseFixedPriceItem with only ItemID + SKU — every other field is
    left untouched.  Listing age, watchers, listing_id, and price are preserved.

    *marketplace_id* (e.g. 'EBAY_MOTORS') selects the Trading API SiteID for
    *listing_id*'s actual marketplace; defaults to EBAY_US (todo #1214 follow-up).
    """
    xml_body = f'''<?xml version="1.0" encoding="utf-8"?>
<ReviseFixedPriceItemRequest xmlns="{_NS}">
  <Item>
    <ItemID>{_xml_escape(listing_id)}</ItemID>
    <SKU>{_xml_escape(new_sku)}</SKU>
  </Item>
</ReviseFixedPriceItemRequest>'''
    trading_call(cfg, 'ReviseFixedPriceItem', xml_body,
                site_id=_resolve_site_id(marketplace_id))
    log.info('ReviseFixedPriceItem: listing %s custom label → %s', listing_id, new_sku)


def get_api_access_rules(cfg: Dict[str, Any],
                         marketplace_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Call GetAPIAccessRules and return usage info for GetBestOffers.
    Returns a list of dicts with keys: call_name, daily_limit, daily_used,
    hourly_limit, hourly_used.  Returns [] on any error.

    *marketplace_id* (e.g. 'EBAY_MOTORS') selects the Trading API SiteID;
    defaults to EBAY_US.
    """
    xml_body = (
        f'<?xml version="1.0" encoding="utf-8"?>\n'
        f'<GetAPIAccessRulesRequest xmlns="{_NS}">\n'
        f'  <DetailLevel>ReturnAll</DetailLevel>\n'
        f'</GetAPIAccessRulesRequest>'
    )
    try:
        root = trading_call(cfg, 'GetAPIAccessRules', xml_body, timeout=30,
                            site_id=_resolve_site_id(marketplace_id))
    except Exception as exc:
        log.warning('GetAPIAccessRules failed: %s', exc)
        return []

    results = []
    alloc_list = root.find(_t('CallAllocationList'))
    if alloc_list is None:
        return []

    for alloc in alloc_list.findall(_t('APICallAllocation')):
        name_el = alloc.find(_t('CallName'))
        if name_el is None:
            continue

        def _int(tag: str, el: 'ET.Element' = alloc) -> int:
            child = el.find(_t(tag))
            try:
                return int(child.text or '0') if child is not None else 0
            except (ValueError, TypeError):
                return 0

        results.append({
            'call_name':    (name_el.text or '').strip(),
            'daily_limit':  _int('DailyLimit'),
            'daily_used':   _int('DailyUsage'),
            'hourly_limit': _int('HourlyLimit'),
            'hourly_used':  _int('HourlyUsage'),
        })
    return results


# ---------------------------------------------------------------------------
# Best Offer API (PP-OFFER-001)
# ---------------------------------------------------------------------------

def _offer_from_xml(offer_el: ET.Element) -> Dict[str, Any]:
    """Extract a flat dict from a <BestOffer> element."""
    def txt(tag: str) -> str:
        el = offer_el.find(_t(tag))
        return (el.text or '').strip() if el is not None else ''

    item_el = offer_el.find(_t('Item'))
    listing_id = ''
    title = ''
    sku = ''
    listing_price: Optional[float] = None
    if item_el is not None:
        id_el = item_el.find(_t('ItemID'))
        listing_id = (id_el.text or '').strip() if id_el is not None else ''
        title_el = item_el.find(_t('Title'))
        title = (title_el.text or '').strip() if title_el is not None else ''
        sku_el = item_el.find(_t('SKU'))
        if sku_el is None:
            sku_el = item_el.find(_t('CustomLabel'))
        sku = (sku_el.text or '').strip() if sku_el is not None else ''
        selling = item_el.find(_t('SellingStatus'))
        if selling is not None:
            price_el = selling.find(_t('CurrentPrice'))
            if price_el is not None and price_el.text:
                try:
                    listing_price = float(price_el.text)
                except ValueError:
                    pass

    buyer_el = offer_el.find(_t('Buyer'))
    buyer = ''
    if buyer_el is not None:
        uid_el = buyer_el.find(_t('UserID'))
        buyer = (uid_el.text or '').strip() if uid_el is not None else ''

    offer_price: Optional[float] = None
    price_el = offer_el.find(_t('Price'))
    if price_el is not None and price_el.text:
        try:
            offer_price = float(price_el.text)
        except ValueError:
            pass

    return {
        'offer_id': txt('BestOfferID'),
        'listing_id': listing_id,
        'title': title,
        'sku': sku,
        'buyer': buyer,
        'offer_price': offer_price,
        'listing_price': listing_price,
        'status': txt('BestOfferStatus'),
        'expiry': txt('ExpirationTime'),
    }


def get_best_offers(
    cfg: Dict[str, Any],
    listing_id: Optional[str] = None,
    status: str = 'Pending',
    marketplace_id: Optional[str] = None,
) -> Generator[Dict[str, Any], None, None]:
    """Yield incoming Best Offers from GetBestOffers.

    listing_id: filter to a specific listing (omit for all items).
    status: 'Pending' | 'All' | 'Declined' | 'Accepted'.
    marketplace_id (e.g. 'EBAY_MOTORS') selects the Trading API SiteID;
    defaults to EBAY_US. When omitting listing_id (all items), this call is
    per-site — full multi-marketplace coverage needs one call per known
    marketplace_id (todo #1214 follow-up; not done automatically here).
    Handles pagination automatically.
    """
    page = 1
    total_pages = 1

    while page <= total_pages:
        item_id_line = f'  <ItemID>{listing_id}</ItemID>\n' if listing_id else ''
        xml_body = (
            f'<?xml version="1.0" encoding="utf-8"?>\n'
            f'<GetBestOffersRequest xmlns="{_NS}">\n'
            f'{item_id_line}'
            f'  <BestOfferStatus>{status}</BestOfferStatus>\n'
            f'  <DetailLevel>ReturnAll</DetailLevel>\n'
            f'  <Pagination>\n'
            f'    <EntriesPerPage>200</EntriesPerPage>\n'
            f'    <PageNumber>{page}</PageNumber>\n'
            f'  </Pagination>\n'
            f'</GetBestOffersRequest>'
        )

        root = _trading_call_retrying(cfg, 'GetBestOffers', xml_body, timeout=60,
                                      site_id=_resolve_site_id(marketplace_id))

        pagination = root.find(_t('PaginationResult'))
        if pagination is not None:
            total_pages = int(pagination.findtext(_t('TotalNumberOfPages')) or '1')

        offer_array = root.find(_t('BestOfferArray'))
        if offer_array is not None:
            for offer_el in offer_array.findall(_t('BestOffer')):
                yield _offer_from_xml(offer_el)

        page += 1


def respond_to_best_offer(
    cfg: Dict[str, Any],
    offer_id: str,
    listing_id: str,
    action: str,
    counter_price: Optional[float] = None,
    marketplace_id: Optional[str] = None,
) -> None:
    """Submit a response to a Best Offer via RespondToBestOffer.

    action: 'Accept' | 'Decline' | 'Counter'
    counter_price: required when action='Counter'.
    marketplace_id (e.g. 'EBAY_MOTORS') selects the Trading API SiteID for
    *listing_id*'s actual marketplace; defaults to EBAY_US (todo #1214 follow-up).
    Raises RuntimeError on API failure.
    """
    counter_block = ''
    if action == 'Counter' and counter_price is not None:
        counter_block = (
            f'  <CounterOfferPrice currencyID="USD">{counter_price:.2f}</CounterOfferPrice>\n'
            f'  <CounterOfferQuantity>1</CounterOfferQuantity>\n'
        )
    xml_body = (
        f'<?xml version="1.0" encoding="utf-8"?>\n'
        f'<RespondToBestOfferRequest xmlns="{_NS}">\n'
        f'  <ItemID>{_xml_escape(listing_id)}</ItemID>\n'
        f'  <BestOfferID>{_xml_escape(offer_id)}</BestOfferID>\n'
        f'  <Action>{_xml_escape(action)}</Action>\n'
        f'{counter_block}'
        f'</RespondToBestOfferRequest>'
    )
    trading_call(cfg, 'RespondToBestOffer', xml_body, timeout=30,
                site_id=_resolve_site_id(marketplace_id))
    log.info('RespondToBestOffer: offer=%s listing=%s action=%s', offer_id, listing_id, action)
