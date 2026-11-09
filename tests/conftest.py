from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.models
from app.core.config import get_settings
from app.db.session import get_session
from app.main import app

PASSWORD = "test-password"


@pytest.fixture
def app_password(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    settings = get_settings()
    monkeypatch.setattr(settings, "app_password", PASSWORD)
    yield
    monkeypatch.setattr(settings, "app_password", "")


@pytest.fixture
def engine() -> Generator[Engine, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(engine: Engine) -> Generator[TestClient, None, None]:
    def override_get_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
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
