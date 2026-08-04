"""audit#COHESION-2026-07 / todo #1276 — build_listing_description() embedded
ai_desc (LLM-generated / third-party product-lookup prose) verbatim,
unescaped, into the live eBay listing HTML. Escape it with html.escape()
(the codebase's existing convention for this, per http_server.py), the
same as any other untrusted/item-derived string interpolated into HTML.
bp_html (operator-configured boilerplate) and pl (the picklist line) are
untouched by this fix.
"""

from tgw.ebay.description import build_listing_description

_CFG = {}  # use default boilerplate


def test_html_special_chars_in_description_are_escaped():
    item = {'description': 'Nice <b>item</b>, buy now!', 'sku': 'tgw1'}
    html_out = build_listing_description(item, _CFG)

    assert 'Nice &lt;b&gt;item&lt;/b&gt;, buy now!' in html_out
    # no real <b> element introduced by the description text
    assert '<p>Nice <b>item</b>' not in html_out


def test_script_tag_in_description_cannot_inject_html():
    item = {'description': '<script>alert(1)</script>', 'sku': 'tgw1'}
    html_out = build_listing_description(item, _CFG)

    assert '<script>' not in html_out
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html_out


def test_plain_prose_description_output_unchanged():
    item = {
        'description': 'A gently used widget in excellent condition.',
        'sku': 'tgw1',
        'location': 'A1',
        'title': 'Widget',
    }
    html_out = build_listing_description(item, _CFG)

    assert html_out.startswith(
        '<p>A gently used widget in excellent condition.</p>'
    )
    # plain prose has no special chars, so escaping is a no-op here
    assert '&lt;' not in html_out
    assert '&gt;' not in html_out


def test_draft_listing_description_takes_precedence_and_is_escaped():
    item = {
        'description': 'fallback, ignored',
        'draft_listing': {'description': 'Draft <i>desc</i> & more'},
        'sku': 'tgw1',
    }
    html_out = build_listing_description(item, _CFG)

    assert 'Draft &lt;i&gt;desc&lt;/i&gt; &amp; more' in html_out
    assert '<i>desc</i>' not in html_out
