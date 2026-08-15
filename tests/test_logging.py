"""Tests for tgw.logging."""

from __future__ import annotations

import logging
import logging.handlers
import tempfile
from pathlib import Path

from tgw.logging import get_logger, log_event, setup_logging


def test_setup_logging_returns_logger():
    with tempfile.TemporaryDirectory() as d:
        # Reset configured flag for test isolation
        import tgw.logging as tl
        tl._configured = False
        logger = setup_logging('tgw.test', log_root=Path(d), console=False)
        assert isinstance(logger, logging.Logger)
        tl._configured = False  # reset after test


def test_console_only_logging_opens_no_file_handler():
    import tgw.logging as tl

    root_logger = logging.getLogger('tgw')
    tl._configured = False
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()
    setup_logging('tgw.embedded-test', log_root=None, console=True)
    assert not any(
        isinstance(handler, logging.handlers.RotatingFileHandler)
        for handler in root_logger.handlers
    )
    tl._configured = False
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()


def test_get_logger_namespaced():
    logger = get_logger('myworker')
    assert logger.name == 'tgw.myworker'


def test_get_logger_already_namespaced():
    logger = get_logger('tgw.myworker')
    assert logger.name == 'tgw.myworker'


def test_log_event_does_not_raise():
    log_event('test.event', sku='tgw20260101000000001', count=5)


def test_json_log_path_no_extension_falls_back_to_tgw_jsonl():
    """log_file without a '.log' substring must not collide with the
    main log file — regression test for #1290."""
    import tgw.logging as tl

    root_logger = logging.getLogger('tgw')
    with tempfile.TemporaryDirectory() as d:
        tl._configured = False
        for h in list(root_logger.handlers):
            root_logger.removeHandler(h)
        setup_logging(
            'x', log_root=Path(d), log_file='custom', json_file=True,
            console=False,
        )
        file_paths = {
            Path(h.baseFilename)
            for h in root_logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        }
        tl._configured = False
        for h in list(root_logger.handlers):
            root_logger.removeHandler(h)

    assert Path(d) / 'custom' in file_paths
    assert Path(d) / 'tgw.jsonl' in file_paths
    assert Path(d) / 'custom.jsonl' not in file_paths
    assert len(file_paths) == 2


def test_json_log_path_with_log_extension_produces_matching_jsonl():
    import tgw.logging as tl

    root_logger = logging.getLogger('tgw')
    with tempfile.TemporaryDirectory() as d:
        tl._configured = False
        for h in list(root_logger.handlers):
            root_logger.removeHandler(h)
        setup_logging(
            'x', log_root=Path(d), log_file='custom.log', json_file=True,
            console=False,
        )
        file_paths = {
            Path(h.baseFilename)
            for h in root_logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        }
        tl._configured = False
        for h in list(root_logger.handlers):
            root_logger.removeHandler(h)

    assert Path(d) / 'custom.log' in file_paths
    assert Path(d) / 'custom.jsonl' in file_paths
    assert len(file_paths) == 2
