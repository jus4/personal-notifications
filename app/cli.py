import argparse
import asyncio
import json
import logging
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError
from sqlmodel import Session, col, select

from app.db.session import engine
from app.models.common import utcnow
from app.models.source import Source
from app.scrapers.config import SourceScraperConfig
from app.services.scrape import run_source

logger = logging.getLogger(__name__)

JOB_KINDS = ["jobs"]


def _absolute_http_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise argparse.ArgumentTypeError(
            f"invalid URL {value!r}: expected an absolute http:// or https:// URL"
        )
    return value


def _config_json(value: str) -> dict[str, Any]:
    try:
        config = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise argparse.ArgumentTypeError("config must be a JSON object")
    try:
        SourceScraperConfig.model_validate(config)
    except ValidationError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="notification-service")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sources = subparsers.add_parser("sources", help="Manage scrape sources")
    source_subparsers = sources.add_subparsers(dest="sources_command", required=True)

    add_parser = source_subparsers.add_parser(
        "add", help="Create or update a source by slug"
    )
    add_parser.add_argument("--slug", required=True)
    add_parser.add_argument("--kind", default="jobs", choices=JOB_KINDS)
    add_parser.add_argument("--adapter", required=True)
    add_parser.add_argument("--url", required=True, type=_absolute_http_url)
    add_parser.add_argument(
        "--config-json",
        type=_config_json,
        help="Source scraper config as a JSON object",
    )
    add_parser.add_argument(
        "--disabled",
        action="store_true",
        help="Save the source as disabled",
    )

    list_parser = source_subparsers.add_parser("list", help="List sources")
    list_parser.add_argument("--kind", choices=JOB_KINDS)

    scrape_parser = subparsers.add_parser("scrape", help="Run scrape sources")
    scrape_parser.add_argument("--source", dest="source_slug")
    scrape_parser.add_argument("--kind", default="jobs", choices=JOB_KINDS)

    return parser


def _add_source(args: argparse.Namespace) -> int:
    with Session(engine) as session:
        source = session.exec(select(Source).where(Source.slug == args.slug)).first()
        now = utcnow()

        config = args.config_json

        if source is None:
            source = Source(
                slug=args.slug,
                kind=args.kind,
                adapter=args.adapter,
                url=args.url,
                enabled=not args.disabled,
                config=config,
                created_at=now,
                updated_at=now,
            )
        else:
            source.kind = args.kind
            source.adapter = args.adapter
            source.url = args.url
            source.enabled = not args.disabled
            if config is not None:
                source.config = config
            source.updated_at = now

        session.add(source)
        session.commit()

    logger.info("Source %s saved", args.slug)
    return 0


def _list_sources(args: argparse.Namespace) -> int:
    with Session(engine) as session:
        statement = select(Source).order_by(Source.slug)
        if args.kind:
            statement = statement.where(Source.kind == args.kind)

        sources = session.exec(statement).all()

    for source in sources:
        print(
            f"{source.slug}\t{source.kind}\t{source.adapter}\t"
            f"enabled={source.enabled}\t{source.url or ''}"
        )

    return 0


async def _scrape(args: argparse.Namespace) -> int:
    with Session(engine) as session:
        statement = select(Source).where(col(Source.enabled).is_(True))
        if args.source_slug:
            statement = statement.where(col(Source.slug) == args.source_slug)
        else:
            statement = statement.where(col(Source.kind) == args.kind)

        source_ids = [
            source.id
            for source in session.exec(statement.order_by(col(Source.slug))).all()
            if source.id is not None
        ]

    if not source_ids:
        logger.error("No enabled sources matched")
        return 1

    failed = False
    for source_id in source_ids:
        with Session(engine) as session:
            source = session.get(Source, source_id)
            if source is None:
                continue

            try:
                result = await run_source(session, source)
            except Exception:
                session.rollback()
                logger.exception("Scrape failed for %s", source.slug)
                failed = True
                continue

            logger.info(
                "Scraped %s: fetched=%s inserted=%s updated=%s skipped=%s failed=%s",
                result.source,
                result.fetched,
                result.inserted,
                result.updated,
                result.skipped,
                result.failed,
            )

    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "sources":
        if args.sources_command == "add":
            return _add_source(args)
        if args.sources_command == "list":
            return _list_sources(args)

    if args.command == "scrape":
        return asyncio.run(_scrape(args))

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
