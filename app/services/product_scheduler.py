import asyncio
import logging

from sqlmodel import Session

from app.db.session import engine
from app.services.product_scrape import run_product_scrape
from app.services.scrape_lock import scrape_lock

logger = logging.getLogger(__name__)

PRODUCT_SCRAPE_CHECK_SECONDS = 300


async def run_product_scrape_scheduler(interval_seconds: int) -> None:
    while True:
        if not scrape_lock.locked():
            async with scrape_lock:
                with Session(engine) as session:
                    try:
                        batch = await run_product_scrape(
                            session,
                            interval_seconds=interval_seconds,
                        )
                    except Exception:
                        logger.exception("Scheduled product scrape failed")
                    else:
                        logger.info(
                            "Scheduled product scrape finished: sources=%s failures=%s",
                            batch.source_count,
                            len(batch.failures),
                        )

        await asyncio.sleep(min(interval_seconds, PRODUCT_SCRAPE_CHECK_SECONDS))
