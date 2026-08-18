"""audit#COHESION-2026-07 / todo #1281 — readiness_html() interpolated
item-derived ReadinessField.value (title, category, condition, price note)
directly into HTML with zero escaping. Escape it with html.escape() (the
codebase's existing convention for this, per http_server.py and #1276's
fix to description.py). f.label is always a hardcoded string literal
passed at each _f(...) call site in EbayReadinessChecker.check() -- never
derived from item data -- so it is deliberately left unescaped.
"""

from tgw.readiness import ReadinessField, check_ebay, readiness_html


def _field(value):
    return ReadinessField(
        name="ebay_title",
        label="eBay title",
        status="ok",
        severity="required",
        value=value,
        jump_to="dl-title",
    )


def test_script_tag_in_value_cannot_inject_html():
    html_out = readiness_html([_field("<script>alert(1)</script>")])

    assert "<script>" not in html_out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_out


def test_plain_value_with_no_special_chars_is_byte_identical():
    field = _field("123 · Cell Phones")
    html_out = readiness_html([field])

    expected = (
        '<div id="readiness-checklist" style="margin:0 0 14px 0;border:1px solid #333;'
        'border-radius:6px;overflow:hidden">'
        '<div style="background:#111;padding:6px 10px;font-size:.78em;color:#778;'
        'border-bottom:1px solid #333;font-weight:600">Listing readiness</div>'
        '<a href="#dl-title" style="display:flex;align-items:center;padding:5px 10px;'
        'background:#1a2a1a;border-left:3px solid #4a4;text-decoration:none;'
        'border-bottom:1px solid #1a1a1a">'
        '<span style="margin-right:6px;font-size:.85em;min-width:18px">✅</span>'
        '<span style="color:#ccc;font-size:.85em;flex:1">eBay title</span>'
        '<span style="color:#667;font-size:.82em;margin-left:8px">123 · Cell Phones</span>'
        "</a></div>"
    )
    assert html_out == expected
    assert "&lt;" not in html_out
    assert "&gt;" not in html_out


def test_none_value_still_renders_empty_val_html():
    html_out = readiness_html([_field(None)])

    # the if f.value else "" guard short-circuits before str(f.value)/escape
    # is ever reached, so nothing changes for the None case
    assert (
        '<span style="color:#ccc;font-size:.85em;flex:1">eBay title</span></a>'
        in html_out
    )
    assert 'margin-left:8px">' not in html_out


def test_readiness_accepts_null_price_comps_from_an_existing_offer():
    """A partial offer projection must not turn an item detail page into 500."""
    fields = check_ebay({
        "draft_listing": {
            "title": "Floppy disks",
            "category_id": "80136",
            "condition": "Used",
            "price": None,
        },
        "ebay_offer": {"offer_id": "266759538018", "price_comps": None},
    })

    price = next(field for field in fields if field.name == "ebay_price")
    assert price.status == "missing"


def test_label_remains_unescaped_hardcoded_literal():
    # Deliberate non-fix: f.label is always a hardcoded string literal at
    # each _f(...) call site, never item-derived, so it is not escaped.
    # This assertion pins that assumption so a future change to how label
    # is populated doesn't silently reopen this class of bug unnoticed.
    field = ReadinessField(
        name="x",
        label="<b>not escaped</b>",
        status="ok",
        severity="required",
        value="safe value",
        jump_to="x",
    )
    html_out = readiness_html([field])

    assert "<b>not escaped</b>" in html_out
    assert "&lt;b&gt;" not in html_out
