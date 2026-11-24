import logging

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.db import session as session_module
from app.db.session import check_database_ready


class _BrokenEngine:
    def connect(self) -> object:
        raise OperationalError("SELECT 1", {}, Exception("db down"))


def test_check_database_ready_passes_for_migrated_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32))")
        )
    monkeypatch.setattr(session_module, "engine", engine)

    check_database_ready()

    engine.dispose()


def test_check_database_ready_rejects_unmigrated_database(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = create_engine("sqlite://")
    monkeypatch.setattr(session_module, "engine", engine)

    with (
        caplog.at_level(logging.ERROR, logger="app.db.session"),
        pytest.raises(RuntimeError, match="not migrated"),
    ):
        check_database_ready()

    assert "alembic_version" in caplog.text
    engine.dispose()


def test_check_database_ready_logs_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(session_module, "engine", _BrokenEngine())

    with (
        caplog.at_level(logging.ERROR, logger="app.db.session"),
        pytest.raises(OperationalError),
    ):
        check_database_ready()

    assert "Database connection failed" in caplog.text
