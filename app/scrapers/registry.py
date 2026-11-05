from app.scrapers.base import JobSourceAdapter, ProductSourceAdapter

JOB_ADAPTERS: dict[str, type[JobSourceAdapter]] = {}
PRODUCT_ADAPTERS: dict[str, type[ProductSourceAdapter]] = {}


def register_job_adapter(
    adapter: type[JobSourceAdapter],
) -> type[JobSourceAdapter]:
    JOB_ADAPTERS[adapter.key] = adapter
    return adapter


def register_product_adapter(
    adapter: type[ProductSourceAdapter],
) -> type[ProductSourceAdapter]:
    PRODUCT_ADAPTERS[adapter.key] = adapter
    return adapter
