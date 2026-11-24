import asyncio
from contextlib import nullcontext

import pytest
from sqlmodel import Session, select

from app.core.config import Settings
from app.models.job import Job
from app.models.user_job_delivery import UserJobDelivery
from app.services import notification_scheduler
from app.services.connections import manager
from app.services.notification_scheduler import (
    notify_connected_users,
    run_notification_scheduler,
    send_unread_jobs_for_user,
)

INTERVAL = 60 * 60


class StopScheduler(Exception):
    pass


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "sqlite://",
        "notification_interval_seconds": INTERVAL,
    }
    values.update(overrides)
    return Settings.model_construct(**values)


def test_scheduler_skips_database_when_no_users_are_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise StopScheduler

    monkeypatch.setattr(manager, "user_ids", list)
    monkeypatch.setattr(notification_scheduler.asyncio, "sleep", fake_sleep)

    def session_factory() -> object:
        raise AssertionError("database should not be opened without clients")

    with pytest.raises(StopScheduler):
        asyncio.run(
            run_notification_scheduler(_settings(), session_factory=session_factory)
        )

    assert sleeps == [INTERVAL, INTERVAL]


def test_scheduler_notifies_each_interval_for_connected_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    notify_calls: list[tuple[object, bool]] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise StopScheduler

    async def fake_notify(session: object, *, send_empty: bool) -> None:
        notify_calls.append((session, send_empty))

    monkeypatch.setattr(manager, "user_ids", lambda: [1])
    monkeypatch.setattr(notification_scheduler.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(notification_scheduler, "notify_connected_users", fake_notify)

    with pytest.raises(StopScheduler):
        asyncio.run(
            run_notification_scheduler(
                _settings(),
                session_factory=lambda: nullcontext(object()),
            )
        )

    assert sleeps == [INTERVAL, INTERVAL]
    assert len(notify_calls) == 1
    assert notify_calls[0][1] is False


def test_scheduler_is_disabled_with_zero_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_sleep(seconds: float) -> None:
        raise AssertionError("disabled scheduler must not sleep")

    monkeypatch.setattr(notification_scheduler.asyncio, "sleep", fake_sleep)

    asyncio.run(
        run_notification_scheduler(
            _settings(notification_interval_seconds=0),
            session_factory=lambda: nullcontext(object()),
        )
    )


def test_notify_connected_users_sends_no_new_jobs_when_empty(
    session: Session,
    admin_user: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[tuple[int, dict]] = []

    async def fake_send(user_id: int, response: dict) -> int:
        sent.append((user_id, response))
        return 1

    monkeypatch.setattr(manager, "user_ids", lambda: [1])
    monkeypatch.setattr(manager, "send_to_user", fake_send)

    asyncio.run(notify_connected_users(session, send_empty=True))

    assert sent == [(1, {"type": "no_new_jobs", "status": "success"})]


def test_notify_connected_users_stays_silent_when_empty_and_disabled(
    session: Session,
    admin_user: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[tuple[int, dict]] = []

    async def fake_send(user_id: int, response: dict) -> int:
        sent.append((user_id, response))
        return 1

    monkeypatch.setattr(manager, "user_ids", lambda: [1])
    monkeypatch.setattr(manager, "send_to_user", fake_send)

    asyncio.run(notify_connected_users(session, send_empty=False))

    assert sent == []


def test_notify_connected_users_sends_unread_jobs_once(
    session: Session,
    admin_user: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session.add(Job(external_id="1", source="test", title="New job"))
    session.commit()

    sent: list[tuple[int, dict]] = []

    async def fake_send(user_id: int, response: dict) -> int:
        sent.append((user_id, response))
        return 1

    monkeypatch.setattr(manager, "user_ids", lambda: [1])
    monkeypatch.setattr(manager, "send_to_user", fake_send)

    asyncio.run(notify_connected_users(session, send_empty=False))
    asyncio.run(notify_connected_users(session, send_empty=False))

    assert len(sent) == 1
    assert sent[0][1]["type"] == "new_jobs"
    assert [job["title"] for job in sent[0][1]["data"]["jobs"]] == ["New job"]

    delivery = session.exec(select(UserJobDelivery)).one()
    assert delivery.sent_at is not None


def test_send_unread_jobs_does_not_mark_sent_when_sender_fails(
    session: Session,
    admin_user: None,
) -> None:
    session.add(Job(external_id="1", source="test", title="New job"))
    session.commit()

    async def fail(_: dict) -> bool:
        return False

    asyncio.run(
        send_unread_jobs_for_user(
            session,
            1,
            send=fail,
            send_empty=False,
        )
    )

    assert session.exec(select(UserJobDelivery)).all() == []
