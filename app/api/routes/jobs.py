from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.core.htmx import is_htmx
from app.core.security import is_authenticated
from app.core.templating import templates
from app.db.session import SessionDep
from app.services.jobs import JobFilters, JobRepository

router = APIRouter(tags=["jobs"])

JOBS_PER_PAGE = 25


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(
    request: Request,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    source: Annotated[str | None, Query(max_length=255)] = None,
    posted_from: Annotated[date | None, Query()] = None,
    posted_to: Annotated[date | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
) -> Response:
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)

    filters = JobFilters(
        q=q.strip() if q else None,
        source=source or None,
        posted_from=posted_from,
        posted_to=posted_to,
    )
    repository = JobRepository(session)
    page_result = repository.list_jobs(
        filters,
        page=page,
        per_page=JOBS_PER_PAGE,
    )
    context = {
        "filters": filters,
        "page_result": page_result,
        "sources": repository.list_sources(),
    }

    template = "_jobs_table.html" if is_htmx(request) else "jobs.html"
    return templates.TemplateResponse(request, template, context)
