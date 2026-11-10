from collections.abc import Callable, Generator
from typing import Annotated

from fastapi import Depends
from sqlmodel import Session, create_engine

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.normalized_database_url,
    echo=settings.sql_echo,
)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]

SessionFactory = Callable[[], Session]


def get_session_factory() -> SessionFactory:
    return lambda: Session(engine)


SessionFactoryDep = Annotated[SessionFactory, Depends(get_session_factory)]
