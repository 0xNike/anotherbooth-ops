from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from server.camera import CanonCcapiAdapter, SimulatedCameraAdapter
from server.config import load_config
from server.logging_setup import configure_logging
from server.state_machine import SessionState, SessionStateMachine

configure_logging(log_dir=Path("logs"))
logger = logging.getLogger(__name__)

config = load_config()
fsm = SessionStateMachine(config)


def _build_cameras() -> dict[str, SimulatedCameraAdapter | CanonCcapiAdapter]:
    camera_map: dict[str, SimulatedCameraAdapter | CanonCcapiAdapter] = {}
    for room_id, room in config.rooms.items():
        if room.simulated_capture:
            camera_map[room_id] = SimulatedCameraAdapter(room_id)
        else:
            camera_map[room_id] = CanonCcapiAdapter(room_id=room_id, camera_ip=room.camera_ip)
    return camera_map


cameras = _build_cameras()


async def _retention_worker() -> None:
    while True:
        await asyncio.sleep(60)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    retention_task = asyncio.create_task(_retention_worker())
    try:
        yield
    finally:
        retention_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await retention_task


app = FastAPI(title="Another Booth Ops", lifespan=lifespan)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "profile": config.profile})


@app.get("/admin/status")
async def admin_status() -> JSONResponse:
    camera_health = {}
    for room_id, cam in cameras.items():
        health = await cam.health_check()
        camera_health[room_id] = {
            "status": health.status.value,
            "last_error": health.last_error,
            "consecutive_failures": health.consecutive_failures,
        }
    return JSONResponse({"session_state": fsm.ctx.current_state.value, "camera_health": camera_health})


@app.post("/admin/session/start")
async def start_session() -> JSONResponse:
    session_id = uuid.uuid4().hex[:10]
    try:
        ctx = await fsm.start_session(session_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse({"session_id": ctx.session_id, "state": ctx.current_state.value})


@app.post("/admin/session/advance")
async def advance_session() -> JSONResponse:
    if fsm.ctx.current_state == SessionState.IDLE:
        raise HTTPException(status_code=400, detail="No active session")
    state = await fsm.advance()
    return JSONResponse({"state": state.value})


@app.websocket("/ws/{tablet_id}")
async def ws_room(websocket: WebSocket, tablet_id: str) -> None:
    await websocket.accept()
    logger.info("Tablet connected", extra={"event": "tablet_connect", "room_id": tablet_id, "result": "ok"})
    try:
        while True:
            msg = await websocket.receive_json()
            await websocket.send_json({"ack": True, "received": msg})
    except WebSocketDisconnect:
        logger.info("Tablet disconnected", extra={"event": "tablet_disconnect", "room_id": tablet_id, "result": "ok"})
