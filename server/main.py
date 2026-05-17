from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from server.camera import SimulatedCameraAdapter
from server.capture import CaptureError, CaptureService, state_to_room
from server.config import load_config
from server.logging_setup import configure_logging
from server.printing import PrintError, PrintService, SimulatedPrinterAdapter
from server.state_machine import SessionState, SessionStateMachine

configure_logging(log_dir=Path("logs"))
logger = logging.getLogger(__name__)


async def _retention_worker() -> None:
    # Placeholder: scheduled session/file cleanup lands here (see RetentionConfig).
    while True:
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.retention_task = asyncio.create_task(_retention_worker())
    try:
        yield
    finally:
        app.state.retention_task.cancel()


app = FastAPI(title="Another Booth Ops", lifespan=lifespan)
config = load_config()
fsm = SessionStateMachine(config)
cameras: dict[str, SimulatedCameraAdapter] = {
    room_id: SimulatedCameraAdapter(room_id) for room_id in config.rooms
}
capture_service = CaptureService(config, cameras)
printer = SimulatedPrinterAdapter(name=config.printer.name, paper_size=config.printer.paper_size)
print_service = PrintService(capture_service, printer)


class SelectRequest(BaseModel):
    frame_ids: list[str]


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
    printer_health = await printer.health_check()
    return JSONResponse(
        {
            "profile": config.profile,
            "session_state": fsm.ctx.current_state.value,
            "session_id": fsm.ctx.session_id,
            "camera_health": camera_health,
            "printer": {
                "name": printer.name,
                "paper_size": printer.paper_size,
                "status": printer_health.status.value,
                "last_error": printer_health.last_error,
            },
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


@app.post("/admin/session/capture")
async def capture_session() -> JSONResponse:
    state = fsm.ctx.current_state
    room_id = state_to_room(state)
    if room_id is None:
        return JSONResponse(
            {"error": f"No room to capture in state {state.value}"},
            status_code=409,
        )
    try:
        capture = await capture_service.capture_room(fsm.ctx.session_id, room_id)
    except CaptureError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    return JSONResponse(
        {
            "session_id": fsm.ctx.session_id,
            "room_id": room_id,
            "files": capture.files,
            "shots_captured": len(capture.files),
        }
    )


@app.get("/admin/session/{session_id}/manifest")
async def session_manifest(session_id: str) -> JSONResponse:
    return JSONResponse(
        {"session_id": session_id, "rooms": capture_service.manifest(session_id)}
    )


@app.get("/admin/session/{session_id}/frames")
async def session_frames(session_id: str) -> JSONResponse:
    return JSONResponse(
        {"session_id": session_id, "frames": capture_service.frames(session_id)}
    )


@app.post("/admin/session/select")
async def select_session(req: SelectRequest) -> JSONResponse:
    state = fsm.ctx.current_state
    if state != SessionState.SELECTING:
        return JSONResponse(
            {"error": f"Selection only allowed in SELECTING state (now {state.value})"},
            status_code=409,
        )
    try:
        chosen = print_service.select(fsm.ctx.session_id, req.frame_ids)
    except PrintError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    return JSONResponse({"session_id": fsm.ctx.session_id, "selected": chosen})


@app.post("/admin/session/print")
async def print_session() -> JSONResponse:
    state = fsm.ctx.current_state
    if state != SessionState.PRINTING:
        return JSONResponse(
            {"error": f"Printing only allowed in PRINTING state (now {state.value})"},
            status_code=409,
        )
    try:
        job = await print_service.print_session(fsm.ctx.session_id)
    except PrintError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    return JSONResponse(
        {
            "session_id": fsm.ctx.session_id,
            "job_id": job.job_id,
            "status": job.status,
            "paper_size": job.paper_size,
            "frame_ids": job.frame_ids,
        }
    )


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
