import logging

import pytest

from app.core.logging import configure_logging, resolve_log_level


def test_resolve_log_level() -> None:
    assert resolve_log_level("debug") == logging.DEBUG
    assert resolve_log_level(" INFO ") == logging.INFO

    with pytest.raises(ValueError, match="unknown log level"):
        resolve_log_level("verbose")


def test_configure_logging_sets_level_and_is_idempotent() -> None:
    root = logging.getLogger()
    original_level = root.level
    original_handlers = list(root.handlers)
    try:
        configure_logging("DEBUG")
        handlers = list(root.handlers)
        assert root.level == logging.DEBUG

        configure_logging("INFO")
        assert root.level == logging.INFO
        assert root.handlers == handlers
    finally:
        root.setLevel(original_level)
        for handler in list(root.handlers):
            if handler not in original_handlers:
                root.removeHandler(handler)
