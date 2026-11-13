"""Product scrapers live here.

Product sources implement ``ProductSourceAdapter`` and register themselves
with ``register_product_adapter``.
"""

from app.scrapers.products.bergfreunde import BergfreundeProductAdapter

__all__ = ["BergfreundeProductAdapter"]
