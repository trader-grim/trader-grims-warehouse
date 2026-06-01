"""Tests for tgw.logging."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from tgw.logging import setup_logging, log_event, get_logger


def test_setup_logging_returns_logger():
    with tempfile.TemporaryDirectory() as d:
        # Reset configured flag for test isolation
        import tgw.logging as tl
        tl._configured = False
        logger = setup_logging('tgw.test', log_root=Path(d), console=False)
        assert isinstance(logger, logging.Logger)
        tl._configured = False  # reset after test


def test_get_logger_namespaced():
    logger = get_logger('myworker')
    assert logger.name == 'tgw.myworker'


def test_get_logger_already_namespaced():
    logger = get_logger('tgw.myworker')
    assert logger.name == 'tgw.myworker'


def test_log_event_does_not_raise():
    log_event('test.event', sku='tgw20260101000000001', count=5)
