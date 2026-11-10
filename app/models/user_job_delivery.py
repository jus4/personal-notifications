from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


class UserJobDelivery(SQLModel, table=True):
    __tablename__: ClassVar[Any] = "user_job_deliveries"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_user_job_deliveries"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    job_id: int = Field(foreign_key="jobs.id", index=True)
    sent_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    shown_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
