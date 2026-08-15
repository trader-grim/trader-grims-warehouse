"""tgw.seo.title.enhance_title — oversized titles are flagged, not silently
truncated (Dave, 2026-07-10): the full text must survive so the operator can
trim it interactively in the editor, same workflow as eBay's own bulk-CSV
tool. The actual "never let this reach eBay" guard lives in ebay_stage.py.
"""
from tgw.seo.title import _MAX_TITLE, enhance_title


def test_oversized_ai_title_with_no_brand_is_flagged_and_preserved():
    # tgw202605051752520/051913468/051936445 all had an 81+ char AI-generated
    # title with nothing to inject a brand around. It must survive intact so
    # the operator can trim it by hand in the editor.
    long_title = 'A' * 90
    result = enhance_title(long_title, product_lookup=None, item_specifics=None)
    assert result['title'] == long_title
    assert 'title_too_long' in result['flags']


def test_mpn_append_never_pushes_title_over_the_cap():
    base = 'B' * 75
    result = enhance_title(base, product_lookup={'mpn': 'XL-9000-TOO-LONG-TO-FIT'})
    assert len(result['title']) <= _MAX_TITLE


def test_title_within_limit_is_left_untouched():
    title = 'Vintage Ceramic Bowl With Blue Flowers'
    result = enhance_title(title, product_lookup=None, item_specifics=None)
    assert len(result['title']) <= _MAX_TITLE
    assert 'title_too_long' not in result['flags']
