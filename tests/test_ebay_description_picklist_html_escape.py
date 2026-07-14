"""todo #1367 / PP-COHESION-001 — build_listing_description() embedded the
picklist line (pl = picklist_line(item), built from item title) verbatim,
unescaped, into the live eBay listing HTML. Same trust boundary and fix
pattern as #1276 (ai_desc). Escape pl only at the HTML-embedding call site
in build_listing_description(); picklist_line()'s own return value must
stay unescaped raw text for its other consumers (warehouse picking,
Google Sheet sync, tgw.source convention).
"""

from tgw.ebay.description import build_listing_description, picklist_line

_CFG = {}  # use default boilerplate


def test_html_special_chars_in_title_are_escaped_in_picklist_line_html():
    item = {'title': 'Nice <b>item</b>', 'sku': 'tgw123', 'draft_listing': {}}
    html_out = build_listing_description(item, _CFG)

    assert 'Nice &lt;b&gt;item&lt;/b&gt;' in html_out
    # no real <b> element introduced by the title text
    assert '<b>item</b>' not in html_out


def test_script_tag_in_title_cannot_inject_html_via_picklist_line():
    item = {
        'title': '<script>alert(1)</script>',
        'sku': 'tgw123',
        'draft_listing': {},
    }
    html_out = build_listing_description(item, _CFG)

    assert '<script>' not in html_out


def test_picklist_line_standalone_output_unchanged_and_unescaped():
    item = {
        'title': '<script>alert(1)</script>',
        'sku': 'tgw123',
        'location': 'A1',
        'ebay_listing': {},
    }
    pl = picklist_line(item)

    # picklist_line() itself must remain raw/unescaped for its other
    # consumers (warehouse picking, Google Sheet sync, tgw.source)
    assert pl == 'tgw-pl::=::A1:=:<script>alert(1)</script>:=:tgw123:=:null'


def test_plain_ascii_title_output_unchanged():
    item = {
        'description': 'A gently used widget in excellent condition.',
        'sku': 'tgw1',
        'location': 'A1',
        'title': 'Widget',
    }
    html_out = build_listing_description(item, _CFG)

    assert html_out.endswith(
        '<p>tgw-pl::=::A1:=:Widget:=:tgw1:=:null</p>'
    )
    assert '&lt;' not in html_out
    assert '&gt;' not in html_out
