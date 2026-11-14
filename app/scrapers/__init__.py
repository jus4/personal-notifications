from app.scrapers.jobs import DuunitoriJobAdapter
from app.scrapers.products import (
    AminoporssiProductAdapter,
    BergfreundeProductAdapter,
    EpictvProductAdapter,
    HardloopProductAdapter,
    TradeinnProductAdapter,
    VarusteProductAdapter,
)
from app.scrapers.registry import JOB_ADAPTERS, PRODUCT_ADAPTERS

__all__ = [
    "JOB_ADAPTERS",
    "PRODUCT_ADAPTERS",
    "AminoporssiProductAdapter",
    "BergfreundeProductAdapter",
    "DuunitoriJobAdapter",
    "EpictvProductAdapter",
    "HardloopProductAdapter",
    "TradeinnProductAdapter",
    "VarusteProductAdapter",
]
