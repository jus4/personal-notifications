import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.core.config import get_settings
from app.core.security import require_ws_api_key
from app.db.session import SessionFactory, SessionFactoryDep
from app.models.user import User
from app.services.connections import manager
from app.services.jobs import JobRepository, serialize_job

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/{user_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: int,
    session_factory: SessionFactoryDep,
    _: Annotated[None, Depends(require_ws_api_key)],
) -> None:
    with session_factory() as session:
        if session.get(User, user_id) is None:
            await websocket.close(code=1008)
            return
        jobs = JobRepository(session).list_unread_jobs_for_user(
            user_id,
            resend_after_seconds=settings.job_reshow_after_seconds,
        )
        payloads = [serialize_job(job) for job in jobs]
        job_ids = [job.id for job in jobs if job.id is not None]

    await manager.connect(user_id, websocket)
    logger.info("User #%s connected", user_id)
    await manager.send_json_msg(
        websocket,
        manager.create_response({"user_id": user_id}),
    )

    if payloads:
        await _send_jobs(websocket, user_id, payloads, job_ids, session_factory)

    try:
        while True:
            message = await websocket.receive_text()
            print(message)
            await _handle_client_message(user_id, message, session_factory)
    except WebSocketDisconnect:
        manager.disconnect(user_id, websocket)
        logger.info("User #%s disconnected", user_id)


async def _send_jobs(
    websocket: WebSocket,
    user_id: int,
    payloads: list[dict],
    job_ids: list[int],
    session_factory: SessionFactory,
) -> None:
    try:
        await manager.send_json_msg(
            websocket,
            {"type": "new_jobs", "status": "success", "data": {"jobs": payloads}},
        )
    except RuntimeError:
        manager.disconnect(user_id, websocket)
        return

    with session_factory() as session:
        JobRepository(session).mark_jobs_sent(user_id, job_ids)
        session.commit()


async def _handle_client_message(
    user_id: int,
    message: str,
    session_factory: SessionFactory,
) -> None:
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        logger.warning("User #%s sent invalid JSON: %r", user_id, message)
        return

    if not isinstance(payload, dict):
        logger.warning("User #%s sent non-object JSON: %r", user_id, payload)
        return

    if payload.get("type") == "jobs_shown":
        job_ids = payload.get("job_ids")
        if not isinstance(job_ids, list) or not all(
            isinstance(job_id, int) for job_id in job_ids
        ):
            logger.warning("User #%s sent invalid jobs_shown: %r", user_id, payload)
            return

        with session_factory() as session:
            JobRepository(session).mark_jobs_shown(user_id, job_ids)
            session.commit()
        logger.info("User #%s marked %s jobs shown", user_id, len(job_ids))
        return

    logger.info("User #%s sent: %s", user_id, payload)
