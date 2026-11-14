import json
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.core.security import is_authenticated
from app.core.templating import templates
from app.db.session import SessionDep
from app.models.source import Source
from app.scrapers.http import validate_url
from app.scrapers.registry import JOB_ADAPTERS, PRODUCT_ADAPTERS
from app.services.sources import SOURCE_KINDS, SourceRepository, parse_source_config

router = APIRouter(tags=["sources"])


def _login_redirect() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=303)


def _source_form(source: Source | None = None) -> dict[str, Any]:
    if source is None:
        return {
            "id": None,
            "slug": "",
            "kind": "jobs",
            "adapter": "",
            "url": "",
            "enabled": True,
            "config_json": "",
        }

    config_json = ""
    if source.config:
        config_json = json.dumps(
            source.config,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )

    return {
        "id": source.id,
        "slug": source.slug,
        "kind": source.kind,
        "adapter": source.adapter,
        "url": source.url or "",
        "enabled": source.enabled,
        "config_json": config_json,
    }


def _sources_context(
    repository: SourceRepository,
    *,
    kind: str | None = None,
    edit_id: int | None = None,
    error: str | None = None,
    form: dict[str, Any] | None = None,
    saved: str | None = None,
    deleted: str | None = None,
) -> dict[str, Any]:
    edit_source = None
    if edit_id is not None:
        edit_source = repository.get_source(edit_id)
        if edit_source is None:
            raise HTTPException(status_code=404, detail="Source not found")

    if form is None:
        form = _source_form(edit_source)

    return {
        "sources": repository.list_sources(kind),
        "kind": kind,
        "form": form,
        "is_edit": form.get("id") is not None,
        "job_adapters": sorted(JOB_ADAPTERS),
        "product_adapters": sorted(PRODUCT_ADAPTERS),
        "error": error,
        "saved": saved,
        "deleted": deleted,
    }


def _validate_source_form(
    *,
    slug: str,
    kind: str,
    adapter: str,
    url: str,
    config_json: str,
) -> dict[str, Any] | None:
    cleaned_slug = slug.strip()
    if not cleaned_slug:
        raise ValueError("Slug is required")
    if len(cleaned_slug) > 255:
        raise ValueError("Slug must be 255 characters or fewer")

    if kind not in SOURCE_KINDS:
        raise ValueError("Kind must be either jobs or products")

    cleaned_adapter = adapter.strip()
    if not cleaned_adapter:
        raise ValueError("Adapter is required")
    adapters = JOB_ADAPTERS if kind == "jobs" else PRODUCT_ADAPTERS
    if cleaned_adapter not in adapters:
        available = ", ".join(sorted(adapters)) or "none"
        raise ValueError(
            f"Unknown {kind} adapter {cleaned_adapter!r}. Available: {available}"
        )

    target = url.strip()
    if kind == "jobs" and not target:
        raise ValueError("URL is required for job sources")
    if target:
        validate_url(target)

    return parse_source_config(config_json)


@router.get("/sources", response_class=HTMLResponse)
def sources_page(
    request: Request,
    session: SessionDep,
    kind: Annotated[str | None, Query()] = None,
    edit: Annotated[int | None, Query(ge=1)] = None,
    saved: Annotated[str | None, Query(max_length=255)] = None,
    deleted: Annotated[str | None, Query(max_length=255)] = None,
) -> Response:
    if not is_authenticated(request):
        return _login_redirect()

    if kind is not None and kind not in SOURCE_KINDS:
        raise HTTPException(status_code=404, detail="Source kind not found")

    repository = SourceRepository(session)
    context = _sources_context(
        repository,
        kind=kind,
        edit_id=edit,
        saved=saved,
        deleted=deleted,
    )
    return templates.TemplateResponse(request, "sources.html", context)


@router.post("/sources", response_class=HTMLResponse)
def save_source(
    request: Request,
    session: SessionDep,
    source_id: Annotated[int | None, Form()] = None,
    slug: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "jobs",
    adapter: Annotated[str, Form()] = "",
    url: Annotated[str, Form()] = "",
    enabled: Annotated[bool, Form()] = False,
    config_json: Annotated[str, Form()] = "",
) -> Response:
    if not is_authenticated(request):
        return _login_redirect()

    repository = SourceRepository(session)
    form = {
        "id": source_id,
        "slug": slug,
        "kind": kind,
        "adapter": adapter,
        "url": url,
        "enabled": enabled,
        "config_json": config_json,
    }

    try:
        config = _validate_source_form(
            slug=slug,
            kind=kind,
            adapter=adapter,
            url=url,
            config_json=config_json,
        )
        source = repository.save_source(
            source_id=source_id,
            slug=slug,
            kind=kind,
            adapter=adapter.strip(),
            url=url.strip() or None,
            enabled=enabled,
            config=config,
        )
    except (TypeError, ValueError) as exc:
        context = _sources_context(
            repository,
            kind=kind if kind in SOURCE_KINDS else None,
            error=str(exc),
            form=form,
        )
        return templates.TemplateResponse(
            request,
            "sources.html",
            context,
            status_code=400,
        )

    return RedirectResponse(url=f"/sources?saved={quote(source.slug)}", status_code=303)


@router.post("/sources/{source_id}/delete", response_class=HTMLResponse)
def delete_source(
    request: Request,
    session: SessionDep,
    source_id: int,
) -> Response:
    if not is_authenticated(request):
        return _login_redirect()

    repository = SourceRepository(session)
    source = repository.get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    slug = source.slug
    try:
        repository.delete_source(source)
    except ValueError as exc:
        context = _sources_context(repository, error=str(exc))
        return templates.TemplateResponse(
            request,
            "sources.html",
            context,
            status_code=400,
        )

    return RedirectResponse(
        url=f"/sources?deleted={quote(slug)}",
        status_code=303,
    )
