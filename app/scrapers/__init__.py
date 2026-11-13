from app.scrapers.jobs import DuunitoriJobAdapter
from app.scrapers.products import BergfreundeProductAdapter
from app.scrapers.registry import JOB_ADAPTERS, PRODUCT_ADAPTERS

__all__ = [
    "JOB_ADAPTERS",
    "PRODUCT_ADAPTERS",
    "BergfreundeProductAdapter",
    "DuunitoriJobAdapter",
]
