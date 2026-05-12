from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse

from server.camera import SimulatedCameraAdapter
from server.config import load_config
from server.logging_setup import configure_logging
from server.state_machine import SessionStateMachine

configure_logging(log_dir=Path("logs"))
logger = logging.getLogger(__name__)

app = FastAPI(title="Another Booth Ops")
config = load_config()
fsm = SessionStateMachine(config)
cameras: dict[str, SimulatedCameraAdapter] = {
    room_id: SimulatedCameraAdapter(room_id) for room_id in config.rooms
}


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "profile": config.profile})


@app.get("/admin/status")
async def admin_status() -> JSONResponse:
    camera_health: dict[str, dict[str, object]] = {}
    for room_id, adapter in cameras.items():
        h = await adapter.health_check()
        camera_health[room_id] = {
            "status": h.status.value,
            "last_error": h.last_error,
            "consecutive_failures": h.consecutive_failures,
        }
    return JSONResponse(
        {
            "profile": config.profile,
            "session_state": fsm.ctx.current_state.value,
            "session_id": fsm.ctx.session_id,
            "camera_health": camera_health,
        }
    )


@app.post("/admin/session/start")
async def start_session() -> JSONResponse:
    session_id = uuid.uuid4().hex[:10]
    ctx = await fsm.start_session(session_id)
    return JSONResponse({"session_id": ctx.session_id, "state": ctx.current_state.value})


@app.post("/admin/session/advance")
async def advance_session() -> JSONResponse:
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
    except Exception:
        logger.warning("Tablet disconnected", extra={"event": "tablet_disconnect", "room_id": tablet_id, "result": "warning"})


async def _retention_worker() -> None:
    while True:
        await asyncio.sleep(60)


@app.on_event("startup")
async def startup_event() -> None:
    app.state.retention_task = asyncio.create_task(_retention_worker())


@app.on_event("shutdown")
async def shutdown_event() -> None:
    task = app.state.retention_task
    task.cancel()
