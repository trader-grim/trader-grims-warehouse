# model-currency

A standalone, dependency-free tool that answers one question:

> **Which LLMs are live right now and worth handing to a coding agent?**

It queries three upstream catalogues, filters out everything that is deprecated
or unsuitable for agentic coding, and prints a ranked list — free models first.

## Why it is standalone

This package has **zero third-party dependencies** and imports nothing
project-specific. It is meant to be lifted wholesale into another repository:
copy the `model_currency/` directory (and, if you want them, `tests/`), and it
works. Configuration is entirely via CLI flags and environment variables.

```
tools/model-currency/
├── model_currency/            the package
│   ├── sources/               one module per upstream (openrouter, groq, models.dev)
│   ├── catalog.py             collect(): fetch + cache + offline fallback
│   ├── filters.py             is a model coding-appropriate?
│   ├── ranking.py             score + sort, best first
│   ├── cache.py               short-TTL on-disk cache (~6h)
│   ├── fallback.py            loader for the checked-in offline list
│   └── fallback_models.json   the checked-in offline list
└── tests/                     run with `tgw` NOT on the path
```

## Sources

| source        | endpoint                              | auth                          |
|---------------|---------------------------------------|-------------------------------|
| OpenRouter    | `https://openrouter.ai/api/v1/models` | none (`OPENROUTER_API_KEY` used if set) |
| Groq          | `https://api.groq.com/openai/v1/models` | `GROQ_API_KEY` (required)    |
| models.dev    | `https://models.dev/api.json`         | none — this is the database the opencode CLI uses for its model list |

If a source can't be reached, a fresh cache entry is used; failing that, a stale
one; failing that, the source is skipped. If **every** source is unreachable the
tool prints the checked-in fallback list and says so on stderr.

## Filtering — "coding-appropriate"

A model is kept only if it is:

- **live** — not flagged inactive, not past its published expiry date;
- **tool-use capable** — exposes function / tool calling;
- **big enough context** — ≥ 32,768 tokens (`--min-context`);
- **a general model** — not an embedding / speech / moderation / image model;
- **not tiny** — a stated parameter count below 7B (`--min-params-b`) is dropped.

## Ranking

`score = 1000·free + 100·tool_use + context/10k + 5·reasoning`, sorted
descending, ties broken by provider then model id. Free + tool-use dominate;
context window is the tie-breaker.

## CLI

```console
$ model-currency
sources — live: openrouter, groq, models.dev
14 free coding-appropriate model(s), best first:

  #  PROVIDER         MODEL                                  CONTEXT  TOOLS FREE SOURCE
  1  groq             openai/gpt-oss-120b                    131,072  yes   yes  groq+models.dev
  2  openrouter       qwen/qwen3-coder:free                  262,144  yes   yes  openrouter
  ...

$ model-currency --json          # machine-readable: {schema, offline, sources, models:[...]}
$ model-currency --include-paid   # also show models that cost money
$ model-currency --refresh        # ignore the cache
$ model-currency --explain        # also print what was rejected and why
$ model-currency --source groq --source openrouter
$ model-currency --models-dev-provider all   # widen models.dev beyond the opencode default set
```

When offline, stderr carries a clear line:

```
offline — using fallback list (as of 2026-09-06)
```

Exit code is `0` even offline — the fallback list is a valid answer.

### Environment

| var                       | meaning                                            |
|---------------------------|----------------------------------------------------|
| `OPENROUTER_API_KEY`      | optional; sent to OpenRouter if present            |
| `GROQ_API_KEY`            | required for the Groq source                       |
| `MODEL_CURRENCY_CACHE`    | cache directory (default `~/.cache/model-currency`)|
| `XDG_CACHE_HOME`          | honoured for the default cache location            |

## Library use

```python
from model_currency import collect, rank, is_coding_appropriate

result = collect()                       # CollectResult
models = [m for m in result.models
          if is_coding_appropriate(m)[0] and m.free]
for m in rank(models):
    print(m.provider, m.model_id, m.context, m.score)
```

`ModelInfo` fields: `provider, model_id, context, tool_use, free, deprecated,
source, name, modalities_in, reasoning, notes, score`.

## Tests

The tests must pass with `tgw` **not importable**. Run them in a clean
environment:

```console
$ python -m venv /tmp/mc && /tmp/mc/bin/pip install pytest
$ cd tools/model-currency && /tmp/mc/bin/python -m pytest -q
```

`tests/test_standalone.py` enforces the boundary: no non-stdlib imports, no
reference to `tgw`, and a full run with `tgw` import blocked.

## Refreshing the fallback list

Run `model-currency --json` online, keep the entries you trust that are `free`
and `tool_use`, and paste them into `model_currency/fallback_models.json` with a
new `generated` date.
