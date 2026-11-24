import argparse
import asyncio
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError
from sqlmodel import Session, col, select

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import engine
from app.models.common import utcnow
from app.models.source import Source
from app.scrapers.config import SourceScraperConfig
from app.services.product_scrape import run_product_scrape
from app.services.product_snapshot import (
    export_product_snapshot,
    ingest_product_snapshot,
    read_product_snapshot,
    write_product_snapshot,
)
from app.services.scrape import run_source

logger = logging.getLogger(__name__)

SOURCE_KINDS = ["jobs", "products"]


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
    add_parser.add_argument("--kind", default="jobs", choices=SOURCE_KINDS)
    add_parser.add_argument("--adapter", required=True)
    add_parser.add_argument("--url", type=_absolute_http_url)
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
    list_parser.add_argument("--kind", choices=SOURCE_KINDS)

    scrape_parser = subparsers.add_parser("scrape", help="Run scrape sources")
    scrape_parser.add_argument("--source", dest="source_slug")
    scrape_parser.add_argument("--kind", default="jobs", choices=SOURCE_KINDS)

    products = subparsers.add_parser(
        "products",
        help="One-time product snapshot export and ingest tools",
    )
    product_subparsers = products.add_subparsers(
        dest="products_command",
        required=True,
    )

    export_parser = product_subparsers.add_parser(
        "export",
        help="Export local products to a JSON snapshot",
    )
    export_parser.add_argument(
        "--source",
        action="append",
        dest="source_slugs",
        help="Product source slug to export; may be repeated",
    )
    export_parser.add_argument(
        "--manufacturer",
        action="append",
        help="Manufacturer slug to export; may be repeated",
    )
    export_parser.add_argument(
        "--output",
        type=Path,
        help="Snapshot path (default: data/<source>_products.json)",
    )

    ingest_parser = product_subparsers.add_parser(
        "ingest",
        help="Apply a JSON snapshot to the database without scraping",
    )
    ingest_parser.add_argument("--input", type=Path, required=True)
    ingest_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and plan the ingest, then roll back",
    )

    return parser


def _add_source(args: argparse.Namespace) -> int:
    if args.kind == "jobs" and not args.url:
        raise SystemExit("--url is required for job sources")

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


def _export_products(args: argparse.Namespace) -> int:
    source_slugs = args.source_slugs
    if args.output is not None:
        output = args.output
    elif source_slugs is not None and len(source_slugs) == 1:
        output = Path("data") / f"{source_slugs[0]}_products.json"
    else:
        output = Path("data") / "products_all_sources.json"

    with Session(engine) as session:
        try:
            snapshot = export_product_snapshot(
                session,
                source_slugs=source_slugs,
                manufacturer_slugs=args.manufacturer,
            )
        except Exception:
            logger.exception("Product snapshot export failed")
            return 1

    write_product_snapshot(snapshot, output)
    logger.info(
        "Exported %s products and %s manufacturers from %s sources to %s",
        len(snapshot.products),
        len(snapshot.manufacturers),
        len(snapshot.sources),
        output,
    )
    return 0


def _ingest_products(args: argparse.Namespace) -> int:
    try:
        snapshot = read_product_snapshot(args.input)
    except Exception:
        logger.exception("Could not read product snapshot %s", args.input)
        return 1

    with Session(engine) as session:
        try:
            report = ingest_product_snapshot(
                session,
                snapshot,
                dry_run=args.dry_run,
            )
        except Exception:
            logger.exception("Product snapshot ingest failed")
            return 1

    logger.info(
        "Ingested snapshot%s: source_created=%s manufacturers_created=%s "
        "categories_created=%s products_created=%s products_updated=%s "
        "offers_created=%s offers_updated=%s",
        " (dry run)" if report.dry_run else "",
        report.source_created,
        report.manufacturers_created,
        report.categories_created,
        report.products_created,
        report.products_updated,
        report.offers_created,
        report.offers_updated,
    )
    return 0


async def _scrape_products(args: argparse.Namespace) -> int:
    with Session(engine) as session:
        try:
            batch = await run_product_scrape(
                session,
                force=True,
                source_slug=args.source_slug,
            )
        except Exception:
            logger.exception("Product scrape failed")
            return 1

    if batch.source_count == 0:
        logger.error("No enabled product sources matched")
        return 1

    for result in batch.results:
        logger.info(
            "Scraped products %s: links=%s updated=%s failed=%s",
            result.source,
            result.links,
            result.updated,
            result.failed,
        )
    for failure in batch.failures:
        logger.error("Product scrape failed for %s: %s", failure.source, failure.error)

    failed = bool(batch.failures) or any(result.failed for result in batch.results)
    return 1 if failed else 0


async def _scrape(args: argparse.Namespace) -> int:
    if args.kind == "products":
        return await _scrape_products(args)

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
    configure_logging(get_settings().log_level)
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "sources":
        if args.sources_command == "add":
            return _add_source(args)
        if args.sources_command == "list":
            return _list_sources(args)

    if args.command == "scrape":
        return asyncio.run(_scrape(args))

    if args.command == "products":
        if args.products_command == "export":
            return _export_products(args)
        if args.products_command == "ingest":
            return _ingest_products(args)

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
