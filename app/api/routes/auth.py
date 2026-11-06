from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.core.security import SESSION_AUTH_KEY, is_authenticated, verify_password
from app.core.templating import templates

router = APIRouter(tags=["auth"])


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> Response:
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(request, "index.html")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    if is_authenticated(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    password: Annotated[str, Form()] = "",
) -> Response:
    if verify_password(password):
        request.session[SESSION_AUTH_KEY] = True
        if _is_htmx(request):
            return Response(status_code=204, headers={"HX-Redirect": "/"})
        return RedirectResponse(url="/", status_code=303)

    context = {"error": "Invalid password"}
    if _is_htmx(request):
        return templates.TemplateResponse(
            request,
            "_login_card.html",
            context,
            status_code=401,
        )
    return templates.TemplateResponse(
        request,
        "login.html",
        context,
        status_code=401,
    )


@router.post("/logout")
def logout(request: Request) -> Response:
    request.session.clear()
    if _is_htmx(request):
        return Response(status_code=204, headers={"HX-Redirect": "/login"})
    return RedirectResponse(url="/login", status_code=303)
