from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from starlette.websockets import WebSocketDisconnect

from app.models.common import utcnow
from app.models.job import Job
from app.models.user_job_delivery import UserJobDelivery
from tests.conftest import PASSWORD

HEADERS = {"X-API-Key": PASSWORD}


def test_websocket_requires_api_key(client: TestClient, app_password: None) -> None:
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/1"):
        pass


def test_websocket_accepts_client(
    client: TestClient,
    app_password: None,
) -> None:
    with client.websocket_connect("/ws/1", headers=HEADERS) as websocket:
        response = websocket.receive_json()

        assert response == {"status": "success", "data": {"user_id": 1}}


def test_websocket_rejects_unknown_user(
    client: TestClient,
    app_password: None,
) -> None:
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/ws/999", headers=HEADERS),
    ):
        pass


def test_websocket_rejects_non_numeric_user_id(
    client: TestClient,
    app_password: None,
) -> None:
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/ws/abc", headers=HEADERS),
    ):
        pass


def test_websocket_handles_messages(
    session: Session,
    client: TestClient,
    app_password: None,
) -> None:
    session.add_all(
        [
            Job(external_id=str(index), source="test", title=f"Job {index}")
            for index in range(2)
        ]
    )
    session.commit()
    job_ids = [job.id for job in session.exec(select(Job)).all()]

    with client.websocket_connect("/ws/1", headers=HEADERS) as websocket:
        websocket.receive_json()

        websocket.send_text("not json")
        websocket.send_json({"type": "jobs_shown", "job_ids": job_ids})


def test_websocket_sends_backlog_on_first_connection(
    session: Session,
    client: TestClient,
    app_password: None,
) -> None:
    session.add(
        Job(
            external_id="old",
            source="test",
            title="Old backlog job",
            raw_data={"date_posted": "2020-01-01T00:00:00+00:00"},
        )
    )
    session.commit()

    with client.websocket_connect("/ws/1", headers=HEADERS) as websocket:
        assert websocket.receive_json() == {
            "status": "success",
            "data": {"user_id": 1},
        }
        message = websocket.receive_json()

        assert message["type"] == "new_jobs"
        assert [job["title"] for job in message["data"]["jobs"]] == [
            "Old backlog job"
        ]


def test_websocket_sends_all_backlog_on_first_connection(
    session: Session,
    client: TestClient,
    app_password: None,
) -> None:
    session.add_all(
        [
            Job(external_id=str(index), source="test", title=f"Job {index}")
            for index in range(105)
        ]
    )
    session.commit()

    with client.websocket_connect("/ws/1", headers=HEADERS) as websocket:
        websocket.receive_json()
        message = websocket.receive_json()

        assert message["type"] == "new_jobs"
        assert len(message["data"]["jobs"]) == 105
        assert {job["title"] for job in message["data"]["jobs"]} == {
            f"Job {index}" for index in range(105)
        }


def test_websocket_sends_all_recent_jobs_for_returning_user(
    session: Session,
    client: TestClient,
    app_password: None,
) -> None:
    now = datetime.now(UTC)
    history_job = Job(
        external_id="history",
        source="test",
        title="History job",
        raw_data={"date_posted": now.isoformat()},
    )
    recent_jobs = [
        Job(
            external_id=f"recent-{index}",
            source="test",
            title=f"Recent {index}",
            raw_data={"date_posted": now.isoformat()},
        )
        for index in range(15)
    ]
    session.add_all([history_job, *recent_jobs])
    session.commit()
    assert history_job.id is not None
    session.add(
        UserJobDelivery(
            user_id=1,
            job_id=history_job.id,
            sent_at=utcnow(),
            shown_at=utcnow(),
        )
    )
    session.commit()

    with client.websocket_connect("/ws/1", headers=HEADERS) as websocket:
        websocket.receive_json()
        message = websocket.receive_json()

        assert message["type"] == "new_jobs"
        assert len(message["data"]["jobs"]) == 15
        assert {job["title"] for job in message["data"]["jobs"]} == {
            f"Recent {index}" for index in range(15)
        }


def test_websocket_skips_old_backlog_for_returning_user(
    session: Session,
    client: TestClient,
    app_password: None,
) -> None:
    now = datetime.now(UTC)
    old_job = Job(
        external_id="old",
        source="test",
        title="Old job",
        raw_data={"date_posted": (now - timedelta(days=40)).isoformat()},
    )
    recent_job = Job(
        external_id="recent",
        source="test",
        title="Recent job",
        raw_data={"date_posted": now.isoformat()},
    )
    session.add_all([old_job, recent_job])
    session.commit()
    assert old_job.id is not None
    session.add(
        UserJobDelivery(
            user_id=1,
            job_id=old_job.id,
            sent_at=utcnow(),
            shown_at=utcnow(),
        )
    )
    session.commit()

    with client.websocket_connect("/ws/1", headers=HEADERS) as websocket:
        websocket.receive_json()
        message = websocket.receive_json()

        assert [job["title"] for job in message["data"]["jobs"]] == ["Recent job"]
