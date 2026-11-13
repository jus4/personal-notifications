from collections.abc import Callable, Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

import app.models
from app.core.config import Settings, get_settings
from app.db.session import get_session, get_session_factory
from app.main import app
from app.models.user import User

PASSWORD = "test-password"


def _test_database_url() -> str:
    settings = get_settings()
    if not settings.test_database_url:
        raise RuntimeError(
            "TEST_DATABASE_URL must be set to a dedicated PostgreSQL database"
        )
    test_url = Settings(database_url=settings.test_database_url).normalized_database_url
    if test_url == settings.normalized_database_url:
        raise RuntimeError("TEST_DATABASE_URL must differ from DATABASE_URL")
    return test_url


@pytest.fixture
def app_password(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    settings = get_settings()
    monkeypatch.setattr(settings, "app_password", PASSWORD)
    yield
    monkeypatch.setattr(settings, "app_password", "")


@pytest.fixture
def engine() -> Generator[Engine, None, None]:
    engine = create_engine(_test_database_url())
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


@pytest.fixture
def admin_user(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(User(id=1, name="admin"))
        session.commit()


@pytest.fixture
def client(
    engine: Engine,
    admin_user: None,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    def override_get_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    def override_get_session_factory() -> Callable[[], Session]:
        return lambda: Session(engine)

    settings = get_settings()
    monkeypatch.setattr(settings, "scrape_interval_seconds", 0)
    monkeypatch.setattr(settings, "product_scrape_interval_seconds", 0)

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_session_factory] = override_get_session_factory
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def authenticated_client(
    client: TestClient,
    app_password: None,
) -> TestClient:
    response = client.post(
        "/login",
        data={"password": PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client
