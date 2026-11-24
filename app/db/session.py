import logging
from collections.abc import Callable, Generator
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, create_engine

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()


def _connect_args() -> dict[str, Any]:
    if settings.normalized_database_url.startswith("postgresql"):
        return {"connect_timeout": settings.db_connect_timeout_seconds}
    return {}


engine = create_engine(
    settings.normalized_database_url,
    echo=settings.sql_echo,
    pool_pre_ping=True,
    pool_recycle=settings.db_pool_recycle_seconds,
    connect_args=_connect_args(),
)


def check_database_ready() -> None:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            has_migrations = inspect(connection).has_table("alembic_version")
    except SQLAlchemyError as exc:
        logger.error("Database connection failed: %s", exc)
        raise

    if not has_migrations:
        message = (
            "Database schema is not migrated (alembic_version is missing); "
            "run 'alembic upgrade head'"
        )
        logger.error(message)
        raise RuntimeError(message)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]

SessionFactory = Callable[[], Session]


def get_session_factory() -> SessionFactory:
    return lambda: Session(engine)


SessionFactoryDep = Annotated[SessionFactory, Depends(get_session_factory)]
