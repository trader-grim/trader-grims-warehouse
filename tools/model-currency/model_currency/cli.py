"""``model-currency`` command line."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .cache import DEFAULT_TTL_SECONDS, Cache
from .catalog import DEFAULT_SOURCES, collect
from .filters import DEFAULT_MIN_CONTEXT, DEFAULT_MIN_PARAMS_B, is_coding_appropriate
from .ranking import rank


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="model-currency",
        description="Ranked list of live, coding-appropriate LLM models from "
        "OpenRouter, Groq, and the opencode / models.dev database.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "--source",
        action="append",
        choices=list(DEFAULT_SOURCES),
        metavar="NAME",
        help="restrict to this source (repeatable). Default: all of "
        + ", ".join(DEFAULT_SOURCES),
    )
    p.add_argument(
        "--models-dev-provider",
        action="append",
        metavar="PROVIDER",
        help="models.dev provider id to include (repeatable), or 'all'. "
        "Default: the opencode default provider set.",
    )
    p.add_argument("--min-context", type=int, default=DEFAULT_MIN_CONTEXT,
                   help=f"minimum context window (default {DEFAULT_MIN_CONTEXT})")
    p.add_argument("--min-params-b", type=float, default=DEFAULT_MIN_PARAMS_B,
                   help=f"drop models with a stated size below this many billion "
                        f"params (default {DEFAULT_MIN_PARAMS_B})")
    p.add_argument("--include-paid", action="store_true",
                   help="also show models that cost money (default: free only)")
    p.add_argument("--include-deprecated", action="store_true",
                   help="also show models flagged inactive / past expiry")
    p.add_argument("--unfiltered", action="store_true",
                   help="skip the coding-appropriateness filter entirely")
    p.add_argument("--limit", type=int, default=0, metavar="N",
                   help="show at most N rows (0 = no limit)")

    p.add_argument("--refresh", action="store_true", help="ignore cache, fetch live")
    p.add_argument("--no-cache", action="store_true", help="do not read or write the cache")
    p.add_argument("--cache-ttl", type=float, default=DEFAULT_TTL_SECONDS, metavar="SECONDS",
                   help="cache freshness window in seconds (default 21600 = 6h)")
    p.add_argument("--cache-dir", metavar="PATH", help="override the cache directory")
    p.add_argument("--timeout", type=float, default=15.0, metavar="SECONDS",
                   help="per-request network timeout (default 15)")

    p.add_argument("--json", action="store_true", dest="as_json",
                   help="emit a JSON object on stdout for machine use")
    p.add_argument("--explain", action="store_true",
                   help="in text mode, also list what was rejected and why")
    return p


def _models_dev_providers(values):
    if not values:
        return None
    if any(v.lower() == "all" for v in values):
        return "all"
    return tuple(values)


def _select(models, args):
    """Apply the coding filter (unless --unfiltered) plus the free/deprecated
    switches. Returns (kept, rejected) where rejected is list[(model, reason)]."""
    rejected: list[tuple] = []
    kept = []
    for m in models:
        if not args.unfiltered:
            ok, reason = is_coding_appropriate(
                m,
                min_context=args.min_context,
                min_params_b=args.min_params_b,
                allow_deprecated=args.include_deprecated,
            )
            if not ok:
                rejected.append((m, reason))
                continue
        if m.deprecated and not args.include_deprecated:
            rejected.append((m, "deprecated / past expiry"))
            continue
        if not m.free and not args.include_paid:
            rejected.append((m, "not free"))
            continue
        kept.append(m)
    return kept, rejected


def _render_text(ranked, result, args, rejected, out, err):
    if result.used_fallback:
        print(f"offline — using fallback list ({result.summary()})", file=err)
    elif result.offline:
        print(f"offline — {result.summary()}", file=err)
    else:
        print(f"sources — {result.summary()}", file=err)
        if result.sources_stale:
            print(f"warning: served stale cache for: {', '.join(result.sources_stale)}", file=err)

    if not ranked:
        print("no models matched the filter.", file=out)
        return

    scope = "free" if not args.include_paid else "all"
    print(f"{len(ranked)} {scope} coding-appropriate model(s), best first:\n", file=out)
    header = f"{'#':>3}  {'PROVIDER':<16} {'MODEL':<44} {'CONTEXT':>9}  {'TOOLS':<5} {'FREE':<4} {'SOURCE'}"
    print(header, file=out)
    print("-" * len(header), file=out)
    for i, m in enumerate(ranked, 1):
        print(
            f"{i:>3}  {m.provider[:16]:<16} {m.model_id[:44]:<44} {m.context:>9,}  "
            f"{'yes' if m.tool_use else 'no':<5} {'yes' if m.free else 'no':<4} {m.source}",
            file=out,
        )

    if args.explain and rejected:
        print(f"\nrejected {len(rejected)}:", file=out)
        for m, reason in sorted(rejected, key=lambda t: (t[0].provider, t[0].model_id)):
            print(f"  {m.provider}/{m.model_id}: {reason}", file=out)


def _render_json(ranked, result, args, out):
    payload = {
        "schema": "model-currency/result/v1",
        "tool_version": __version__,
        "offline": result.offline,
        "used_fallback": result.used_fallback,
        "degraded": result.degraded,
        "sources": {
            "ok": sorted(result.sources_ok),
            "cached": sorted(result.sources_cached),
            "stale": sorted(result.sources_stale),
            "failed": result.sources_failed,
        },
        "filter": {
            "min_context": args.min_context,
            "min_params_b": args.min_params_b,
            "free_only": not args.include_paid,
            "include_deprecated": args.include_deprecated,
            "unfiltered": args.unfiltered,
        },
        "count": len(ranked),
        "models": [m.as_dict() for m in ranked],
    }
    json.dump(payload, out, indent=2)
    out.write("\n")


def main(argv: list[str] | None = None, *, out=None, err=None) -> int:
    args = build_parser().parse_args(argv)
    out = out or sys.stdout
    err = err or sys.stderr

    use_cache = not args.no_cache
    cache = Cache(root=args.cache_dir, ttl=args.cache_ttl) if (use_cache and args.cache_dir) else None
    result = collect(
        sources=tuple(args.source) if args.source else DEFAULT_SOURCES,
        models_dev_providers=_models_dev_providers(args.models_dev_provider),
        cache=cache,
        cache_ttl=args.cache_ttl,
        use_cache=use_cache,
        refresh=args.refresh,
        timeout=args.timeout,
    )

    kept, rejected = _select(result.models, args)
    ranked = rank(kept)
    if args.limit and args.limit > 0:
        ranked = ranked[: args.limit]

    if args.as_json:
        _render_json(ranked, result, args, out)
    else:
        _render_text(ranked, result, args, rejected, out, err)

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
