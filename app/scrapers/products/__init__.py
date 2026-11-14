"""Product scrapers live here.

Product sources implement ``ProductSourceAdapter`` and register themselves
with ``register_product_adapter``.
"""

from app.scrapers.products.aminoporssi import AminoporssiProductAdapter
from app.scrapers.products.bergfreunde import BergfreundeProductAdapter
from app.scrapers.products.epictv import EpictvProductAdapter
from app.scrapers.products.hardloop import HardloopProductAdapter
from app.scrapers.products.tradeinn import TradeinnProductAdapter
from app.scrapers.products.varuste import VarusteProductAdapter

__all__ = [
    "AminoporssiProductAdapter",
    "BergfreundeProductAdapter",
    "EpictvProductAdapter",
    "HardloopProductAdapter",
    "TradeinnProductAdapter",
    "VarusteProductAdapter",
]
