"""
tgw.apis.lookup.open_library — ISBN lookup via Open Library.

No authentication required.  36M+ books.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

from .base import LookupResult, now_iso

log = logging.getLogger(__name__)

_ENDPOINT = 'https://openlibrary.org/api/books'
_TIMEOUT  = 10
_UA       = 'TGW-inventory/1.0 (trader-grims-warehouse)'


def lookup(isbn: str, cfg: Dict[str, Any]) -> Optional[LookupResult]:
    """Look up a book by ISBN-10 or ISBN-13. Returns None on miss or error."""
    try:
        resp = requests.get(
            _ENDPOINT,
            params={'bibkeys': f'ISBN:{isbn}', 'jscmd': 'data', 'format': 'json'},
            headers={'User-Agent': _UA},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as exc:
        log.warning('open_library: request failed for ISBN %s: %s', isbn, exc)
        return None

    key = f'ISBN:{isbn}'
    book = data.get(key)
    if not book:
        log.debug('open_library: no result for ISBN %s', isbn)
        return None

    title      = book.get('title', '')
    authors    = ', '.join(a.get('name', '') for a in book.get('authors', []))
    publishers = ', '.join(p.get('name', '') for p in book.get('publishers', []))
    subjects   = ', '.join(s.get('name', '') for s in book.get('subjects', [])[:5])
    cover_url  = book.get('cover', {}).get('medium', '')
    pub_date   = book.get('publish_date', '')

    description_parts = []
    if authors:
        description_parts.append(f'By {authors}')
    if publishers:
        description_parts.append(f'Published by {publishers}')
    if pub_date:
        description_parts.append(pub_date)
    description = '. '.join(description_parts)

    log.info('open_library: hit for ISBN %s — %r', isbn, title[:60])
    return LookupResult(
        source      = 'open_library',
        fetched_at  = now_iso(),
        title       = title,
        brand       = authors,      # author = brand for books
        description = description,
        isbn        = isbn,
        category    = subjects,
        image_url   = cover_url,
        extra       = {'raw': book},
    )
