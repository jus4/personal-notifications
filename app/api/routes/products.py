from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.core.htmx import is_htmx
from app.core.security import is_authenticated
from app.core.templating import templates
from app.db.session import SessionDep
from app.services.product_scrape import ProductScrapeBatchResult, run_product_scrape
from app.services.products import ProductRepository
from app.services.scrape_lock import scrape_lock

router = APIRouter(tags=["products"])

PRODUCTS_PER_PAGE = 25


def _products_context(
    repository: ProductRepository,
    *,
    page: int = 1,
    q: str | None = None,
    scrape_batch: ProductScrapeBatchResult | None = None,
) -> dict[str, object]:
    return {
        "page_result": repository.list_products(
            page=page,
            per_page=PRODUCTS_PER_PAGE,
            q=q,
        ),
        "q": q,
        "scrape_batch": scrape_batch,
        "ran_scrape": scrape_batch is not None,
    }


def _detail_context(
    repository: ProductRepository,
    product_id: int,
    *,
    error: str | None = None,
) -> dict[str, object] | None:
    detail = repository.get_product_detail(product_id)
    if detail is None:
        return None
    return {
        "detail": detail,
        "shops": repository.list_product_shops(),
        "error": error,
    }


@router.get("/products", response_class=HTMLResponse)
def products_page(
    request: Request,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
) -> Response:
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)

    repository = ProductRepository(session)
    context = _products_context(
        repository,
        page=page,
        q=q.strip() if q else None,
    )

    template = "_products_table.html" if is_htmx(request) else "products.html"
    return templates.TemplateResponse(request, template, context)


@router.post("/products", response_class=HTMLResponse)
def create_product(
    request: Request,
    session: SessionDep,
    title: Annotated[str, Form()] = "",
    image_url: Annotated[str, Form()] = "",
) -> Response:
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)

    repository = ProductRepository(session)
    try:
        product = repository.create_product(title=title, image_url=image_url)
    except ValueError as exc:
        context = _products_context(repository)
        context["error"] = str(exc)
        return templates.TemplateResponse(
            request,
            "products.html",
            context,
            status_code=400,
        )

    return RedirectResponse(url=f"/products/{product.id}", status_code=303)


@router.post("/products/scrape", response_class=HTMLResponse)
async def scrape_products(request: Request, session: SessionDep) -> Response:
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)

    async with scrape_lock:
        batch = await run_product_scrape(session, force=True)

    if is_htmx(request):
        return templates.TemplateResponse(
            request,
            "_product_scrape_results.html",
            {"scrape_batch": batch},
        )

    repository = ProductRepository(session)
    context = _products_context(repository, scrape_batch=batch)
    return templates.TemplateResponse(request, "products.html", context)


@router.get("/products/{product_id}", response_class=HTMLResponse)
def product_detail(
    request: Request,
    session: SessionDep,
    product_id: int,
) -> Response:
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)

    context = _detail_context(ProductRepository(session), product_id)
    if context is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return templates.TemplateResponse(request, "product_detail.html", context)


@router.post("/products/{product_id}/sources", response_class=HTMLResponse)
def add_product_source(
    request: Request,
    session: SessionDep,
    product_id: int,
    source_id: Annotated[int, Form()],
    url: Annotated[str, Form()] = "",
) -> Response:
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)

    repository = ProductRepository(session)
    try:
        repository.add_product_source(
            product_id=product_id,
            source_id=source_id,
            url=url.strip(),
        )
    except ValueError as exc:
        context = _detail_context(repository, product_id, error=str(exc))
        if context is None:
            raise HTTPException(status_code=404, detail="Product not found") from exc
        return templates.TemplateResponse(
            request,
            "product_detail.html",
            context,
            status_code=400,
        )

    return RedirectResponse(url=f"/products/{product_id}", status_code=303)


@router.post(
    "/products/{product_id}/sources/{link_id}/delete",
    response_class=HTMLResponse,
)
def remove_product_source(
    request: Request,
    session: SessionDep,
    product_id: int,
    link_id: int,
) -> Response:
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)

    removed = ProductRepository(session).remove_product_source(
        product_id=product_id,
        link_id=link_id,
    )
    if not removed:
        raise HTTPException(status_code=404, detail="Product source not found")
    return RedirectResponse(url=f"/products/{product_id}", status_code=303)
