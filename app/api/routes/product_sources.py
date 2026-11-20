import logging
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.core.htmx import is_htmx
from app.core.security import is_authenticated
from app.core.templating import templates
from app.db.session import SessionDep
from app.scrapers.base import ScrapedProduct
from app.scrapers.http import validate_url
from app.services.product_scrape import (
    ProductMatchResult,
    compare_scraped_product,
    fetch_product_preview,
)
from app.services.products import ProductRepository, ProductSourceRow
from app.services.scrape_lock import scrape_lock

logger = logging.getLogger(__name__)

router = APIRouter(tags=["product-sources"])


def _login_redirect() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=303)


def _page_context(
    repository: ProductRepository,
    *,
    show_all: bool = False,
    error: str | None = None,
    saved: str | None = None,
    test_row: ProductSourceRow | None = None,
    match: ProductMatchResult | None = None,
    scraped: ScrapedProduct | None = None,
) -> dict[str, object]:
    return {
        "rows": repository.list_product_source_rows(show_all=show_all),
        "show_all": show_all,
        "error": error,
        "saved": saved,
        "test_row_id": test_row.link.id if test_row is not None else None,
        "match": match,
        "scraped": scraped,
    }


def _test_error_response(
    request: Request,
    repository: ProductRepository,
    *,
    row: ProductSourceRow,
    url: str,
    message: str,
    status_code: int,
) -> Response:
    if is_htmx(request):
        return templates.TemplateResponse(
            request,
            "_product_source_test_result.html",
            {"error": message, "url": url},
            status_code=status_code,
        )

    context = _page_context(
        repository,
        show_all=True,
        error=message,
        test_row=row,
    )
    return templates.TemplateResponse(
        request,
        "product_sources.html",
        context,
        status_code=status_code,
    )


@router.get("/product-sources", response_class=HTMLResponse)
def product_sources_page(
    request: Request,
    session: SessionDep,
    show_all: Annotated[bool, Query()] = False,
    saved: Annotated[str | None, Query(max_length=255)] = None,
) -> Response:
    if not is_authenticated(request):
        return _login_redirect()

    repository = ProductRepository(session)
    context = _page_context(
        repository,
        show_all=show_all,
        saved=saved,
    )
    return templates.TemplateResponse(request, "product_sources.html", context)


@router.post("/product-sources/{link_id}/url", response_class=HTMLResponse)
def update_product_source_url(
    request: Request,
    session: SessionDep,
    link_id: int,
    url: Annotated[str, Form()] = "",
) -> Response:
    if not is_authenticated(request):
        return _login_redirect()

    repository = ProductRepository(session)
    try:
        link = repository.update_product_source_url(link_id=link_id, url=url)
    except ValueError as exc:
        row = repository.get_product_source_row(link_id)
        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Product source not found",
            ) from exc

        context = _page_context(
            repository,
            show_all=True,
            error=str(exc),
            test_row=row,
        )
        return templates.TemplateResponse(
            request,
            "product_sources.html",
            context,
            status_code=400,
        )

    return RedirectResponse(
        url=f"/product-sources?saved={quote(str(link.id))}",
        status_code=303,
    )


@router.post("/product-sources/{link_id}/test", response_class=HTMLResponse)
async def test_product_source_url(
    request: Request,
    session: SessionDep,
    link_id: int,
    url: Annotated[str, Form()] = "",
) -> Response:
    if not is_authenticated(request):
        return _login_redirect()

    repository = ProductRepository(session)
    row = repository.get_product_source_row(link_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Product source not found")

    target = url.strip()
    try:
        validate_url(target)
    except ValueError as exc:
        return _test_error_response(
            request,
            repository,
            row=row,
            url=url,
            message=str(exc),
            status_code=400,
        )

    try:
        async with scrape_lock:
            scraped = await fetch_product_preview(row.source, target)
        match = compare_scraped_product(row.product, row.link, scraped)
    except Exception as exc:
        logger.exception("Product source test failed for %s", target)
        return _test_error_response(
            request,
            repository,
            row=row,
            url=url,
            message=str(exc),
            status_code=200,
        )

    if is_htmx(request):
        return templates.TemplateResponse(
            request,
            "_product_source_test_result.html",
            {
                "match": match,
                "scraped": scraped,
                "url": url,
                "error": None,
            },
        )

    context = _page_context(
        repository,
        show_all=True,
        test_row=row,
        match=match,
        scraped=scraped,
    )
    return templates.TemplateResponse(request, "product_sources.html", context)
