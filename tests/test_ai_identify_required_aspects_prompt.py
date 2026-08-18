import tgw.workers.ai_identify as ai_identify


def test_reidentify_uses_exact_required_aspects_for_existing_category(monkeypatch):
    monkeypatch.setattr(
        ai_identify,
        "get_aspects",
        lambda cfg, category_id: [
            {"name": "Artist", "required": True},
            {"name": "Release Title", "required": True},
            {"name": "Format", "required": False},
        ],
    )

    prompt = ai_identify._prompt_for_item(
        {"ebay_category_id": "176983"}, {}, hint="cassette", product_context="",
    )

    assert "Exact required eBay aspects" in prompt
    assert '"Artist"' in prompt
    assert '"Release Title"' in prompt
    assert '"Format"' not in prompt
