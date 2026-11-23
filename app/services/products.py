from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, or_, select

from app.models.category import Category
from app.models.common import utcnow
from app.models.manufacturer import Manufacturer
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
    manufacturer: Manufacturer | None
    category: Category | None


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
class ProductSourceRow:
    link: ProductSource
    source: Source
    product: Product


@dataclass(slots=True)
class ProductDetail:
    product: Product
    sources: list[ProductSourceView]
    sizes: list[str]
    lowest_price: Decimal | None
    currency: str | None
    last_scraped_at: datetime | None
    image_url: str | None
    manufacturer: Manufacturer | None
    category: Category | None


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
                Manufacturer,
                Category,
            )
            .outerjoin(
                ProductSource,
                col(ProductSource.product_id) == col(Product.id),
            )
            .outerjoin(
                Manufacturer,
                col(Product.manufacturer_id) == col(Manufacturer.id),
            )
            .outerjoin(
                Category,
                col(Product.category_id) == col(Category.id),
            )
            .group_by(col(Product.id), col(Manufacturer.id), col(Category.id))
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
                manufacturer=row[6],
                category=row[7],
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

    def get_manufacturer(self, manufacturer_id: int | None) -> Manufacturer | None:
        if manufacturer_id is None:
            return None
        return self.session.get(Manufacturer, manufacturer_id)

    def get_category(self, category_id: int | None) -> Category | None:
        if category_id is None:
            return None
        return self.session.get(Category, category_id)

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
            manufacturer=self.get_manufacturer(product.manufacturer_id),
            category=self.get_category(product.category_id),
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

    def list_product_source_rows(
        self,
        *,
        show_all: bool = False,
    ) -> list[ProductSourceRow]:
        statement = (
            select(ProductSource, Source, Product)
            .join(Source, col(ProductSource.source_id) == col(Source.id))
            .join(Product, col(ProductSource.product_id) == col(Product.id))
            .order_by(
                col(Product.title),
                col(Source.slug),
                col(ProductSource.id),
            )
        )
        if not show_all:
            statement = statement.where(col(ProductSource.last_error).is_not(None))
        return [
            ProductSourceRow(link=row[0], source=row[1], product=row[2])
            for row in self.session.exec(statement).all()
        ]

    def get_product_source_row(self, link_id: int) -> ProductSourceRow | None:
        statement = (
            select(ProductSource, Source, Product)
            .join(Source, col(ProductSource.source_id) == col(Source.id))
            .join(Product, col(ProductSource.product_id) == col(Product.id))
            .where(col(ProductSource.id) == link_id)
        )
        row = self.session.exec(statement).first()
        if row is None:
            return None
        return ProductSourceRow(link=row[0], source=row[1], product=row[2])

    def update_product_source_url(
        self,
        *,
        link_id: int,
        url: str,
    ) -> ProductSource:
        cleaned_url = url.strip()
        validate_url(cleaned_url)

        link = self.session.get(ProductSource, link_id)
        if link is None:
            raise ValueError("Product source not found")

        existing = self.session.exec(
            select(ProductSource).where(
                col(ProductSource.source_id) == link.source_id,
                col(ProductSource.url) == cleaned_url,
                col(ProductSource.id) != link.id,
            )
        ).first()
        if existing is not None:
            raise ValueError("This URL is already linked to another product")

        link.url = cleaned_url
        link.last_error = None
        link.updated_at = utcnow()
        self.session.add(link)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise ValueError("This URL is already linked to another product") from exc
        self.session.refresh(link)
        return link

    def list_export_rows(
        self,
        source_id: int,
        *,
        manufacturer_slugs: list[str] | None = None,
    ) -> list[tuple[Product, Manufacturer | None, Category | None, ProductSource]]:
        statement = (
            select(Product, Manufacturer, Category, ProductSource)
            .join(
                ProductSource,
                col(ProductSource.product_id) == col(Product.id),
            )
            .outerjoin(
                Manufacturer,
                col(Product.manufacturer_id) == col(Manufacturer.id),
            )
            .outerjoin(
                Category,
                col(Product.category_id) == col(Category.id),
            )
            .where(col(ProductSource.source_id) == source_id)
            .order_by(col(Product.id), col(ProductSource.id))
        )
        if manufacturer_slugs:
            statement = statement.where(col(Manufacturer.slug).in_(manufacturer_slugs))
        return [tuple(row) for row in self.session.exec(statement).all()]

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

    def list_product_links(
        self,
        product_id: int,
        *,
        due_before: datetime | None = None,
    ) -> list[ProductSource]:
        statement = select(ProductSource).where(
            col(ProductSource.product_id) == product_id
        )
        if due_before is not None:
            statement = statement.where(
                or_(
                    col(ProductSource.last_scraped_at).is_(None),
                    col(ProductSource.last_scraped_at) < due_before,
                )
            )
        statement = statement.order_by(
            col(ProductSource.source_id),
            col(ProductSource.id),
        )
        return list(self.session.exec(statement).all())

    def create_product(
        self,
        *,
        title: str,
        image_url: str | None = None,
        manufacturer_id: int | None = None,
        category_id: int | None = None,
    ) -> Product:
        cleaned_title = title.strip()
        if not cleaned_title:
            raise ValueError("Product title is required")
        cleaned_image = _clean_optional(image_url)
        if (
            manufacturer_id is not None
            and self.get_manufacturer(manufacturer_id) is None
        ):
            raise ValueError("Manufacturer not found")
        if category_id is not None and self.get_category(category_id) is None:
            raise ValueError("Category not found")

        now = utcnow()
        product = Product(
            title=cleaned_title,
            image_url=cleaned_image,
            manufacturer_id=manufacturer_id,
            category_id=category_id,
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
