from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.core.htmx import is_htmx
from app.core.security import is_authenticated
from app.core.templating import templates
from app.db.session import SessionDep
from app.services.jobs import JobRepository
from app.services.notification_scheduler import scrape_and_notify
from app.services.scrape_lock import scrape_lock

router = APIRouter(tags=["dashboard"])


def _login_redirect() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=303)


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: SessionDep) -> Response:
    if not is_authenticated(request):
        return _login_redirect()

    stats = JobRepository(session).dashboard_stats()
    return templates.TemplateResponse(request, "index.html", {"stats": stats})


@router.post("/scrape", response_class=HTMLResponse)
async def scrape(request: Request, session: SessionDep) -> Response:
    if not is_authenticated(request):
        return _login_redirect()

    async with scrape_lock:
        batch = await scrape_and_notify(session)

    stats = JobRepository(session).dashboard_stats()
    context = {
        "stats": stats,
        "ran_scrape": True,
        "scrape_results": batch.results,
        "scrape_failures": batch.failures,
        "scrape_source_count": batch.source_count,
    }

    template = "_dashboard_content.html" if is_htmx(request) else "index.html"
    return templates.TemplateResponse(request, template, context)
