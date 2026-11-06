from collections.abc import Generator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import require_api_key

PASSWORD = "test-password"


@pytest.fixture(autouse=True)
def configured_password(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    settings = get_settings()
    monkeypatch.setattr(settings, "app_password", PASSWORD)
    yield
    monkeypatch.setattr(settings, "app_password", "")


def test_login_page(client: TestClient) -> None:
    response = client.get("/login")

    assert response.status_code == 200
    assert "Sign in" in response.text
    assert 'id="app-header"' not in response.text


def test_index_redirects_to_login_when_logged_out(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_rejects_invalid_password(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"password": "wrong"},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert "Invalid password" in response.text

    index = client.get("/", follow_redirects=False)
    assert index.status_code == 303


def test_login_success_sets_session_and_shows_message(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"password": PASSWORD},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "notification_session" in client.cookies

    index = client.get("/")
    assert index.status_code == 200
    assert 'id="app-header"' in index.text
    assert "Logged in successfully" in index.text
    assert "Log out" in index.text


def test_login_htmx_success_redirects(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"password": PASSWORD},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == "/"


def test_login_htmx_failure_returns_login_card(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"password": "wrong"},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert "auth-card" in response.text
    assert "Invalid password" in response.text


def test_logout_clears_session(client: TestClient) -> None:
    client.post("/login", data={"password": PASSWORD}, follow_redirects=False)

    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    index = client.get("/", follow_redirects=False)
    assert index.status_code == 303


def test_login_without_configured_password_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "app_password", "")

    response = client.post(
        "/login",
        data={"password": ""},
        follow_redirects=False,
    )

    assert response.status_code == 401


def test_require_api_key_accepts_matching_password() -> None:
    require_api_key(PASSWORD)


def test_require_api_key_rejects_missing_or_wrong_password() -> None:
    with pytest.raises(HTTPException) as missing:
        require_api_key(None)
    assert missing.value.status_code == 401

    with pytest.raises(HTTPException) as wrong:
        require_api_key("wrong")
    assert wrong.value.status_code == 401
