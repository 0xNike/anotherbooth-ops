from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.camera import CameraHealth, CameraStatus, SimulatedCameraAdapter
from server.capture import CaptureError, CaptureService, state_to_room
from server.config import load_config
from server.main import app, config, fsm
from server.state_machine import SessionState


def _service() -> CaptureService:
    cfg = load_config()
    cameras = {r: SimulatedCameraAdapter(r) for r in ("R2", "R3", "R4", "R5")}
    return CaptureService(cfg, cameras)


def test_state_to_room_maps_active_states_only():
    assert state_to_room(SessionState.ACTIVE_R2) == "R2"
    assert state_to_room(SessionState.ACTIVE_R5) == "R5"
    assert state_to_room(SessionState.IDLE) is None
    assert state_to_room(SessionState.SELECTING) is None
    assert state_to_room(SessionState.PRINTING) is None


@pytest.mark.asyncio
async def test_capture_room_shoots_configured_shot_count():
    service = _service()
    shots = service.config.timing.shots_per_room
    capture = await service.capture_room("sess-1", "R2")
    assert capture.shots_requested == shots
    assert len(capture.files) == shots
    assert all(f.endswith(".jpg") for f in capture.files)


@pytest.mark.asyncio
async def test_manifest_accumulates_rooms_per_session():
    service = _service()
    await service.capture_room("sess-1", "R2")
    await service.capture_room("sess-1", "R3")
    manifest = service.manifest("sess-1")
    assert set(manifest) == {"R2", "R3"}
    assert manifest["R2"]["shots_captured"] == manifest["R2"]["shots_requested"]
    # An untouched session has an empty manifest.
    assert service.manifest("sess-other") == {}


@pytest.mark.asyncio
async def test_capture_unknown_room_raises():
    service = _service()
    with pytest.raises(CaptureError, match="R9"):
        await service.capture_room("sess-1", "R9")


@pytest.mark.asyncio
async def test_capture_surfaces_camera_failure_and_leaves_no_manifest():
    # Stub mimics the adapter contract: trigger_shutter raises and the adapter
    # self-reports OFFLINE health (as CanonCCAPIAdapter does on failure).
    class FailingAdapter:
        room_id = "R2"

        async def trigger_shutter(self) -> dict[str, str]:
            raise RuntimeError("shutter jammed")

        async def download_latest(self) -> dict[str, str]:
            raise RuntimeError("no contents")

        async def health_check(self) -> CameraHealth:
            return CameraHealth(
                status=CameraStatus.OFFLINE,
                last_error="shutter jammed",
                consecutive_failures=3,
            )

    service = CaptureService(load_config(), {"R2": FailingAdapter()})
    with pytest.raises(CaptureError, match="OFFLINE"):
        await service.capture_room("sess-1", "R2")
    # A failed capture records nothing.
    assert service.manifest("sess-1") == {}


def test_api_session_capture_walkthrough():
    client = TestClient(app)
    fsm.ctx.current_state = SessionState.IDLE  # reset shared FSM singleton

    sid = client.post("/admin/session/start").json()["session_id"]

    r2 = client.post("/admin/session/capture")
    assert r2.status_code == 200
    assert r2.json()["room_id"] == "R2"
    assert r2.json()["shots_captured"] == config.timing.shots_per_room

    client.post("/admin/session/advance")  # ACTIVE_R2 -> ACTIVE_R3
    r3 = client.post("/admin/session/capture")
    assert r3.status_code == 200
    assert r3.json()["room_id"] == "R3"

    manifest = client.get(f"/admin/session/{sid}/manifest").json()
    assert set(manifest["rooms"]) == {"R2", "R3"}
    assert manifest["rooms"]["R2"]["shots_captured"] == config.timing.shots_per_room


def test_api_capture_rejected_when_no_room_active():
    client = TestClient(app)
    fsm.ctx.current_state = SessionState.IDLE  # reset shared FSM singleton

    resp = client.post("/admin/session/capture")
    assert resp.status_code == 409
    assert "error" in resp.json()
