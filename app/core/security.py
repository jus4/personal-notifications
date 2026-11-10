import secrets
from typing import Annotated

from fastapi import (
    Header,
    HTTPException,
    Request,
    WebSocketException,
    status,
)

from app.core.config import get_settings

SESSION_AUTH_KEY = "authenticated"


def verify_password(candidate: str) -> bool:
    expected = get_settings().app_password
    if not expected:
        return False
    return secrets.compare_digest(candidate.encode(), expected.encode())


def is_authenticated(request: Request) -> bool:
    return request.session.get(SESSION_AUTH_KEY) is True


def require_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    expected = get_settings().app_password
    if (
        not expected
        or x_api_key is None
        or not secrets.compare_digest(x_api_key.encode(), expected.encode())
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )


def require_ws_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    expected = get_settings().app_password
    if (
        not expected
        or x_api_key is None
        or not secrets.compare_digest(x_api_key.encode(), expected.encode())
    ):
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
