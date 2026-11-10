import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from starlette.websockets import WebSocketDisconnect

from app.models.job import Job
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
