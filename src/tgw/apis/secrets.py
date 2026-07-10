"""
tgw.apis.secrets — single facility for LLM/lookup provider API keys.

Dave, 2026-07-09: every provider (openrouter, google, deepseek, anthropic,
discogs, ...) previously had its own loader re-deriving `secrets_root /
'<name>-credentials.json'`, sometimes with a differently-shaped fallback.
One convention now: PROVIDER_API_KEY environment variables. tgw.config.
load_config() sources secrets_root/tgw.env into the process environment at
startup (real env vars always win over the file — useful for a one-off
shell override without touching it). Every loader in the codebase should
call get_api_key() here instead of reading its own credentials.json.

Follow-up planned (todo #1253): extend this facility to interactive shell
use, and to scoped/least-privilege key issuance per confined worker/agent
once Catio isolates workers — a given worker should receive only the keys
its own task needs, not every credential on the host.
"""

from __future__ import annotations

import os


def get_secret(name: str) -> str:
    """Return an arbitrary named secret from the environment — the general
    form behind get_api_key(), for credentials that aren't a single
    PROVIDER_API_KEY (e.g. IGDB_CLIENT_ID/IGDB_CLIENT_SECRET).

    Raises RuntimeError with a clear message if unset — add it to
    secrets_root/tgw.env or export it directly in the environment.
    """
    value = os.environ.get(name, '')
    if not value:
        raise RuntimeError(
            f'{name} not set — add it to secrets_root/tgw.env or export it '
            f'in the environment.'
        )
    return value


def get_api_key(provider: str) -> str:
    """Return the API key for *provider* (e.g. 'anthropic', 'google',
    'deepseek', 'openrouter', 'discogs') from the PROVIDER_API_KEY
    environment variable. See get_secret() for multi-value credentials.
    """
    return get_secret(f'{provider.upper()}_API_KEY')
