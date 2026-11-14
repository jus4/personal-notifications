from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func
from sqlmodel import Session, col, or_, select

from app.models.common import utcnow
from app.models.product import Product
from app.models.product_source import ProductSource
from app.models.source import Source
from app.scrapers.base import ScrapedProduct
from app.scrapers.http import validate_url


@dataclass(slots=True)
class ProductCard:
    product: Product
    source_count: int
    lowest_price: Decimal | None
    currency: str | None
    last_scraped_at: datetime | None
    image_url: str | None


@dataclass(slots=True)
class ProductPage:
    products: list[ProductCard]
    total: int
    page: int
    pages: int
    per_page: int

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def previous_page(self) -> int | None:
        return self.page - 1 if self.has_previous else None

    @property
    def next_page(self) -> int | None:
        return self.page + 1 if self.has_next else None


@dataclass(slots=True)
class ProductSourceView:
    link: ProductSource
    source: Source


@dataclass(slots=True)
class ProductDetail:
    product: Product
    sources: list[ProductSourceView]
    sizes: list[str]
    lowest_price: Decimal | None
    currency: str | None
    last_scraped_at: datetime | None
    image_url: str | None


class ProductRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_products(
        self,
        *,
        page: int = 1,
        per_page: int = 25,
        q: str | None = None,
    ) -> ProductPage:
        count_statement = select(func.count(col(Product.id)))
        statement = (
            select(
                Product,
                func.count(col(ProductSource.id)),
                func.min(col(ProductSource.price)),
                func.max(col(ProductSource.currency)),
                func.max(col(ProductSource.last_scraped_at)),
                func.max(col(ProductSource.image_url)),
            )
            .outerjoin(
                ProductSource,
                col(ProductSource.product_id) == col(Product.id),
            )
            .group_by(col(Product.id))
            .order_by(col(Product.updated_at).desc(), col(Product.id).desc())
        )
        if q:
            condition = col(Product.title).ilike(f"%{q}%")
            count_statement = count_statement.where(condition)
            statement = statement.where(condition)

        total = self.session.exec(count_statement).one()
        pages = (total + per_page - 1) // per_page if total else 0
        page = min(page, pages) if pages else 1

        rows = self.session.exec(
            statement.offset((page - 1) * per_page).limit(per_page)
        ).all()
        products = [
            ProductCard(
                product=row[0],
                source_count=row[1] or 0,
                lowest_price=row[2],
                currency=row[3],
                last_scraped_at=row[4],
                image_url=row[0].image_url or row[5],
            )
            for row in rows
        ]

        return ProductPage(
            products=products,
            total=total,
            page=page,
            pages=pages,
            per_page=per_page,
        )

    def get_product(self, product_id: int) -> Product | None:
        return self.session.get(Product, product_id)

    def get_product_detail(self, product_id: int) -> ProductDetail | None:
        product = self.get_product(product_id)
        if product is None:
            return None

        sources = self.list_product_sources(product_id)
        sizes = sorted(
            {
                size
                for source in sources
                for size in (source.link.sizes or [])
                if isinstance(size, str) and size.strip()
            },
            key=_size_sort_key,
        )
        prices = [
            source.link.price for source in sources if source.link.price is not None
        ]
        scraped_times = [
            source.link.last_scraped_at
            for source in sources
            if source.link.last_scraped_at is not None
        ]
        image_url = product.image_url or next(
            (source.link.image_url for source in sources if source.link.image_url),
            None,
        )

        return ProductDetail(
            product=product,
            sources=sources,
            sizes=sizes,
            lowest_price=min(prices) if prices else None,
            currency=next(
                (source.link.currency for source in sources if source.link.currency),
                None,
            ),
            last_scraped_at=max(scraped_times) if scraped_times else None,
            image_url=image_url,
        )

    def list_product_sources(self, product_id: int) -> list[ProductSourceView]:
        statement = (
            select(ProductSource, Source)
            .join(Source, col(ProductSource.source_id) == col(Source.id))
            .where(col(ProductSource.product_id) == product_id)
            .order_by(col(Source.slug), col(ProductSource.id))
        )
        return [
            ProductSourceView(link=row[0], source=row[1])
            for row in self.session.exec(statement).all()
        ]

    def list_product_shops(self, *, enabled_only: bool = True) -> list[Source]:
        statement = select(Source).where(col(Source.kind) == "products")
        if enabled_only:
            statement = statement.where(col(Source.enabled).is_(True))
        statement = statement.order_by(col(Source.slug))
        return list(self.session.exec(statement).all())

    def list_source_links(
        self,
        source_id: int,
        *,
        due_before: datetime | None = None,
    ) -> list[ProductSource]:
        statement = select(ProductSource).where(
            col(ProductSource.source_id) == source_id
        )
        if due_before is not None:
            statement = statement.where(
                or_(
                    col(ProductSource.last_scraped_at).is_(None),
                    col(ProductSource.last_scraped_at) < due_before,
                )
            )
        statement = statement.order_by(col(ProductSource.id))
        return list(self.session.exec(statement).all())

    def create_product(self, *, title: str, image_url: str | None = None) -> Product:
        cleaned_title = title.strip()
        if not cleaned_title:
            raise ValueError("Product title is required")
        cleaned_image = _clean_optional(image_url)

        now = utcnow()
        product = Product(
            title=cleaned_title,
            image_url=cleaned_image,
            created_at=now,
            updated_at=now,
        )
        self.session.add(product)
        self.session.commit()
        self.session.refresh(product)
        return product

    def add_product_source(
        self,
        *,
        product_id: int,
        source_id: int,
        url: str,
    ) -> ProductSource:
        validate_url(url)
        if self.get_product(product_id) is None:
            raise ValueError("Product not found")

        source = self.session.get(Source, source_id)
        if source is None or source.kind != "products":
            raise ValueError("Product source not found")

        existing = self.session.exec(
            select(ProductSource).where(
                col(ProductSource.source_id) == source_id,
                col(ProductSource.url) == url,
            )
        ).first()
        if existing is not None:
            raise ValueError("This URL is already linked to a product")

        now = utcnow()
        link = ProductSource(
            product_id=product_id,
            source_id=source_id,
            url=url,
            sizes=[],
            created_at=now,
            updated_at=now,
        )
        self.session.add(link)
        self.session.commit()
        self.session.refresh(link)
        return link

    def remove_product_source(self, *, product_id: int, link_id: int) -> bool:
        link = self.session.exec(
            select(ProductSource).where(
                col(ProductSource.id) == link_id,
                col(ProductSource.product_id) == product_id,
            )
        ).first()
        if link is None:
            return False
        self.session.delete(link)
        self.session.commit()
        return True

    def update_scraped_source(
        self,
        link: ProductSource,
        scraped: ScrapedProduct,
        *,
        scraped_at: datetime,
    ) -> None:
        link.external_id = scraped.external_id
        link.title = scraped.title
        link.image_url = scraped.image_url
        link.price = scraped.price
        link.currency = scraped.currency
        link.availability = scraped.availability
        link.sizes = list(scraped.sizes)
        link.raw_data = scraped.raw_data
        link.last_scraped_at = scraped_at
        link.last_error = None
        link.updated_at = scraped_at
        self.session.add(link)

    def mark_source_error(
        self,
        link: ProductSource,
        error: str,
        *,
        updated_at: datetime,
    ) -> None:
        link.last_error = error
        link.updated_at = updated_at
        self.session.add(link)


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _size_sort_key(value: str) -> tuple[int, float, str]:
    try:
        return (0, float(value.replace(",", ".")), value)
    except ValueError:
        return (1, 0.0, value)
