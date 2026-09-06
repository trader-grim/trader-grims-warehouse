from __future__ import annotations

from model_currency.models import ModelInfo
from model_currency.ranking import rank, score


def mk(**kw) -> ModelInfo:
    base = dict(
        provider="p", model_id="p/m", context=100_000, tool_use=True,
        free=True, deprecated=False, source="t",
    )
    base.update(kw)
    return ModelInfo(**base)


def test_free_beats_paid_even_with_less_context():
    free_small = mk(model_id="a/free", free=True, context=40_000)
    paid_big = mk(model_id="b/paid", free=False, context=1_000_000)
    assert score(free_small) > score(paid_big)


def test_tool_use_beats_no_tools():
    assert score(mk(tool_use=True, free=False)) > score(mk(tool_use=False, free=False))


def test_context_is_the_tiebreaker():
    a = mk(model_id="a/x", context=200_000)
    b = mk(model_id="b/x", context=100_000)
    assert score(a) > score(b)


def test_rank_is_sorted_and_stamps_score():
    models = [
        mk(provider="z", model_id="z/small", context=33_000),
        mk(provider="a", model_id="a/big", context=500_000),
        mk(provider="a", model_id="a/paid", free=False, context=500_000),
    ]
    ranked = rank(models)
    assert [m.model_id for m in ranked] == ["a/big", "z/small", "a/paid"]
    assert all(m.score > 0 for m in ranked)
    # stable tie-break: provider then model_id
    tie = rank([mk(provider="b", model_id="b/m"), mk(provider="a", model_id="a/m")])
    assert [m.provider for m in tie] == ["a", "b"]


def test_rank_does_not_mutate_input():
    m = mk()
    rank([m])
    assert m.score == 0.0
