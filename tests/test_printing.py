from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.camera import SimulatedCameraAdapter
from server.capture import CaptureService
from server.config import load_config
from server.main import app, fsm
from server.printing import PrintError, PrintService, SimulatedPrinterAdapter
from server.state_machine import SessionState


def _print_service() -> PrintService:
    cfg = load_config()
    cameras = {r: SimulatedCameraAdapter(r) for r in ("R2", "R3", "R4", "R5")}
    capture = CaptureService(cfg, cameras)
    printer = SimulatedPrinterAdapter(name=cfg.printer.name, paper_size=cfg.printer.paper_size)
    return PrintService(capture, printer)


# --- printer adapter -------------------------------------------------------

@pytest.mark.asyncio
async def test_printer_health_starts_online():
    printer = SimulatedPrinterAdapter(name="DNP DS-RX1HS", paper_size="4x6")
    assert (await printer.health_check()).status.value == "ONLINE"


@pytest.mark.asyncio
async def test_printer_submit_job_marks_printed():
    printer = SimulatedPrinterAdapter(name="DNP DS-RX1HS", paper_size="4x6")
    job = await printer.submit_job(["R2-1", "R2-2"])
    assert job.status == "PRINTED"
    assert job.paper_size == "4x6"
    assert job.frame_ids == ["R2-1", "R2-2"]
    assert job.job_id


# --- capture frame ids -----------------------------------------------------

@pytest.mark.asyncio
async def test_capture_frames_have_stable_ids():
    service = _print_service()
    await service.capture.capture_room("sess-1", "R2")
    frames = service.capture.frames("sess-1")
    assert [f["frame_id"] for f in frames] == ["R2-1", "R2-2", "R2-3"]
    assert all(f["room_id"] == "R2" for f in frames)


# --- selection -------------------------------------------------------------

@pytest.mark.asyncio
async def test_select_validates_and_dedupes():
    service = _print_service()
    await service.capture.capture_room("sess-1", "R2")
    chosen = service.select("sess-1", ["R2-2", "R2-2", "R2-1"])
    assert chosen == ["R2-2", "R2-1"]  # deduped, order preserved
    assert service.selection("sess-1") == ["R2-2", "R2-1"]


@pytest.mark.asyncio
async def test_select_rejects_unknown_frame():
    service = _print_service()
    await service.capture.capture_room("sess-1", "R2")
    with pytest.raises(PrintError, match="R9-1"):
        service.select("sess-1", ["R2-1", "R9-1"])


def test_select_rejects_empty():
    service = _print_service()
    with pytest.raises(PrintError, match="empty"):
        service.select("sess-1", [])


# --- printing --------------------------------------------------------------

@pytest.mark.asyncio
async def test_print_requires_a_selection():
    service = _print_service()
    with pytest.raises(PrintError, match="No photos selected"):
        await service.print_session("sess-1")


@pytest.mark.asyncio
async def test_print_session_submits_selected_frames():
    service = _print_service()
    await service.capture.capture_room("sess-1", "R2")
    service.select("sess-1", ["R2-1", "R2-3"])
    job = await service.print_session("sess-1")
    assert job.status == "PRINTED"
    assert job.frame_ids == ["R2-1", "R2-3"]
    assert service.job("sess-1") is job


# --- API walkthrough -------------------------------------------------------

def test_api_select_and_print_flow():
    client = TestClient(app)
    fsm.ctx.current_state = SessionState.IDLE  # reset shared FSM singleton

    sid = client.post("/admin/session/start").json()["session_id"]
    client.post("/admin/session/capture")  # capture R2

    # advance R2 -> R3 -> R4 -> R5 -> SELECTING
    for _ in range(4):
        client.post("/admin/session/advance")
    assert client.get("/admin/status").json()["session_state"] == "SELECTING"

    frame_ids = [f["frame_id"] for f in client.get(f"/admin/session/{sid}/frames").json()["frames"]]
    assert frame_ids == ["R2-1", "R2-2", "R2-3"]

    sel = client.post("/admin/session/select", json={"frame_ids": ["R2-1", "R2-3"]})
    assert sel.status_code == 200
    assert sel.json()["selected"] == ["R2-1", "R2-3"]

    client.post("/admin/session/advance")  # SELECTING -> PRINTING
    printed = client.post("/admin/session/print")
    assert printed.status_code == 200
    body = printed.json()
    assert body["status"] == "PRINTED"
    assert body["frame_ids"] == ["R2-1", "R2-3"]
    assert body["paper_size"] == "4x6"


def test_api_select_rejected_outside_selecting_state():
    client = TestClient(app)
    fsm.ctx.current_state = SessionState.IDLE
    client.post("/admin/session/start")  # state -> ACTIVE_R2
    resp = client.post("/admin/session/select", json={"frame_ids": ["R2-1"]})
    assert resp.status_code == 409


def test_api_print_rejected_outside_printing_state():
    client = TestClient(app)
    fsm.ctx.current_state = SessionState.IDLE
    client.post("/admin/session/start")  # state -> ACTIVE_R2
    resp = client.post("/admin/session/print")
    assert resp.status_code == 409
