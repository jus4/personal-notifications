from typing import Any

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app import cli
from app.models.source import Source


def test_sources_add_creates_and_updates(
    monkeypatch: Any,
    engine: Engine,
) -> None:
    monkeypatch.setattr(cli, "engine", engine)

    assert (
        cli.main(
            [
                "sources",
                "add",
                "--slug",
                "duunitori",
                "--adapter",
                "duunitori_json",
                "--url",
                "https://example.test/one",
            ]
        )
        == 0
    )

    with Session(engine) as session:
        source = session.exec(select(Source).where(Source.slug == "duunitori")).one()
        source_id = source.id
        assert source.kind == "jobs"
        assert source.adapter == "duunitori_json"
        assert source.url == "https://example.test/one"
        assert source.enabled is True

    assert (
        cli.main(
            [
                "sources",
                "add",
                "--slug",
                "duunitori",
                "--adapter",
                "duunitori_json",
                "--url",
                "https://example.test/two",
                "--disabled",
            ]
        )
        == 0
    )

    with Session(engine) as session:
        source = session.exec(select(Source).where(Source.slug == "duunitori")).one()
        assert source.id == source_id
        assert source.url == "https://example.test/two"
        assert source.enabled is False


def test_sources_add_rejects_url_without_scheme(
    monkeypatch: Any,
    engine: Engine,
) -> None:
    monkeypatch.setattr(cli, "engine", engine)

    with pytest.raises(SystemExit):
        cli.main(
            [
                "sources",
                "add",
                "--slug",
                "duunitori",
                "--adapter",
                "duunitori_json",
                "--url",
                "example.test/jobs",
            ]
        )
