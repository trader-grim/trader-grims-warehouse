"""
tgw.logging — Centralized logging for the TGW platform.

Every module, worker, and script calls setup_logging() once at startup.
After that, standard logging.getLogger(__name__) works everywhere.

Features:
  - Rotating file handler under /opt/TGW/var/log/
  - Console handler for interactive use
  - Optional structured JSON output for machine-readable logs
  - Consistent format across all TGW components
  - Log level configurable via config or environment variable

Usage:
    from tgw.logging import setup_logging
    setup_logging('tgw.myworker')

    import logging
    log = logging.getLogger(__name__)
    log.info('Worker started')

    # Structured event (machine-readable):
    from tgw.logging import log_event
    log_event('item.processed', sku='tgw20260529...', elapsed=1.2)
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_LOG_ROOT  = Path('/opt/TGW/var/log')
DEFAULT_LOG_FILE  = 'tgw.log'
DEFAULT_MAX_BYTES = 10 * 1024 * 1024   # 10 MB per file
DEFAULT_BACKUPS   = 5                   # keep 5 rotated files
DEFAULT_LEVEL     = 'INFO'

# Environment variable to override log level without touching config
_ENV_LEVEL = 'TGW_LOG_LEVEL'

# ---------------------------------------------------------------------------
# Structured JSON formatter
# ---------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    """
    Emits one JSON object per log line.
    Suitable for log aggregation, grep, and AI parsing.
    """
    def format(self, record: logging.LogRecord) -> str:
        doc: Dict[str, Any] = {
            'ts':      self.formatTime(record, '%Y-%m-%dT%H:%M:%S'),
            'level':   record.levelname,
            'logger':  record.name,
            'msg':     record.getMessage(),
        }
        if record.exc_info:
            doc['exc'] = self.formatException(record.exc_info)
        # Any extra fields passed via log.info('...', extra={'sku': ...})
        for key, val in record.__dict__.items():
            if key not in logging.LogRecord.__dict__ and not key.startswith('_'):
                doc[key] = val
        return json.dumps(doc, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Human-readable formatter
# ---------------------------------------------------------------------------

_CONSOLE_FORMAT = '%(asctime)s %(levelname)-8s %(name)s: %(message)s'
_FILE_FORMAT    = '%(asctime)s %(levelname)-8s %(name)s [%(process)d]: %(message)s'
_DATE_FORMAT    = '%Y-%m-%d %H:%M:%S'


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

_configured = False   # guard against double-setup in the same process


def setup_logging(
    component: str = 'tgw',
    *,
    log_root: Path = DEFAULT_LOG_ROOT,
    log_file: Optional[str] = None,
    level: Optional[str] = None,
    console: bool = True,
    json_file: bool = False,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUPS,
) -> logging.Logger:
    """
    Configure logging for a TGW component.

    Call once at process startup.  Returns the root TGW logger.

    Args:
        component:    Logger name, e.g. 'tgw.queue.launcher'
        log_root:     Directory for log files
        log_file:     Log filename (default: '<component>.log')
        level:        Log level string (default: TGW_LOG_LEVEL env or 'INFO')
        console:      Emit to stderr as well as file
        json_file:    Write a parallel .jsonl structured log file
        max_bytes:    Rotate log file at this size
        backup_count: Number of rotated files to keep
    """
    global _configured

    # Resolve level: arg > env > default
    if level is None:
        level = os.environ.get(_ENV_LEVEL, DEFAULT_LEVEL).upper()
    numeric_level = getattr(logging, level, logging.INFO)

    root_logger = logging.getLogger('tgw')
    if _configured:
        return root_logger

    root_logger.setLevel(numeric_level)

    log_root = Path(log_root)
    log_root.mkdir(parents=True, exist_ok=True)

    filename = log_file or f'{component.replace(".", "_")}.log'

    # --- Rotating file handler (human-readable) ---
    file_path = log_root / filename
    fh = logging.handlers.RotatingFileHandler(
        file_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8',
    )
    fh.setLevel(numeric_level)
    fh.setFormatter(logging.Formatter(_FILE_FORMAT, datefmt=_DATE_FORMAT))
    root_logger.addHandler(fh)

    # --- Structured JSON file handler (optional) ---
    if json_file:
        if filename.endswith('.log'):
            json_filename = filename[:-len('.log')] + '.jsonl'
        else:
            json_filename = 'tgw.jsonl'
        json_path = log_root / json_filename
        jh = logging.handlers.RotatingFileHandler(
            json_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8',
        )
        jh.setLevel(numeric_level)
        jh.setFormatter(JsonFormatter())
        root_logger.addHandler(jh)

    # --- Console handler ---
    if console:
        ch = logging.StreamHandler()
        ch.setLevel(numeric_level)
        ch.setFormatter(logging.Formatter(_CONSOLE_FORMAT, datefmt=_DATE_FORMAT))
        root_logger.addHandler(ch)

    _configured = True
    root_logger.debug('Logging configured: level=%s file=%s', level, file_path)
    return root_logger


# ---------------------------------------------------------------------------
# Structured event logging
# ---------------------------------------------------------------------------

_event_log = logging.getLogger('tgw.events')


def log_event(event: str, level: str = 'info', **fields: Any) -> None:
    """
    Emit a structured event record.

    These go to the normal log as INFO but are designed to be machine-parseable.
    The AI project manager and monitoring tools can grep/parse tgw.events lines.

    Usage:
        log_event('item.scrubbed', sku='tgw20260529...', keys_removed=12)
        log_event('queue.job.completed', queue='itemdata-scrub', elapsed=0.4)
        log_event('ebay.upload.failed', sku='...', error='timeout', level='error')
    """
    record: Dict[str, Any] = {'event': event, 'ts': time.time(), **fields}
    msg = json.dumps(record, ensure_ascii=False, default=str)
    log_fn = getattr(_event_log, level.lower(), _event_log.info)
    log_fn(msg)


# ---------------------------------------------------------------------------
# One-off / ad hoc scripts: announce before doing anything
# ---------------------------------------------------------------------------

def announce_script_run(script_name: str, purpose: str, **fields: Any) -> None:
    """
    Every one-off script (backfill, bulk requeue, remediation, migration —
    anything under scripts/ run by hand, not a systemd worker) must call this
    once at the top of main(), before touching the queue or any data.

    Without this, an anomalous burst of queue activity or API calls has no
    attributable cause in the logs — see 2026-07-04/05 requeue storm
    (invariant E9): a script ran more than once with zero durable trace that
    it had run at all, and the resulting spike looked inexplicable for days.

    Usage:
        announce_script_run(
            'requeue_ebay_draft_402_dead_letters.py',
            'bulk-requeue ebay_draft dead-letters matching a 402 error pattern',
            apply=args.apply, limit=args.limit,
        )
    """
    log_event('script_run_start', script=script_name, purpose=purpose, **fields)


# ---------------------------------------------------------------------------
# Convenience: get a named logger (import shortcut for workers)
# ---------------------------------------------------------------------------

def get_logger(name: str) -> logging.Logger:
    """Return a logger under the tgw namespace."""
    if not name.startswith('tgw.'):
        name = f'tgw.{name}'
    return logging.getLogger(name)
