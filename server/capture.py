from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from server.camera import CameraHealth
from server.config import AppConfig
from server.state_machine import SessionState

logger = logging.getLogger(__name__)


# Active room states map 1:1 onto config room ids.
_STATE_TO_ROOM: dict[SessionState, str] = {
    SessionState.ACTIVE_R2: "R2",
    SessionState.ACTIVE_R3: "R3",
    SessionState.ACTIVE_R4: "R4",
    SessionState.ACTIVE_R5: "R5",
}


def state_to_room(state: SessionState) -> str | None:
    """Return the room id being shot in this state, or None if no room is active."""
    return _STATE_TO_ROOM.get(state)


class CameraAdapter(Protocol):
    """The adapter contract shared by SimulatedCameraAdapter and CanonCCAPIAdapter."""

    room_id: str

    async def trigger_shutter(self) -> dict[str, str]: ...

    async def download_latest(self) -> dict[str, str]: ...

    async def health_check(self) -> CameraHealth: ...


class CaptureError(RuntimeError):
    """Raised when a room capture cannot be completed."""


@dataclass
class RoomCapture:
    room_id: str
    shots_requested: int
    files: list[str] = field(default_factory=list)


class CaptureService:
    """Drives camera adapters per room and records captured files per session."""

    def __init__(self, config: AppConfig, cameras: dict[str, CameraAdapter]) -> None:
        self.config = config
        self.cameras = cameras
        # session_id -> room_id -> RoomCapture
        self._manifests: dict[str, dict[str, RoomCapture]] = {}

    async def capture_room(self, session_id: str, room_id: str) -> RoomCapture:
        """Shoot `shots_per_room` frames in `room_id`; raise CaptureError on any failure.

        A failed capture records nothing in the manifest — it is all-or-nothing per room.
        Adapters own their own health; this service only reads it to report status.
        """
        adapter = self.cameras.get(room_id)
        if adapter is None:
            raise CaptureError(f"No camera configured for room {room_id}")

        shots = self.config.timing.shots_per_room
        capture = RoomCapture(room_id=room_id, shots_requested=shots)

        for shot in range(1, shots + 1):
            try:
                await adapter.trigger_shutter()
                result = await adapter.download_latest()
            except Exception as exc:
                health = await adapter.health_check()
                logger.warning(
                    "Capture failed",
                    extra={
                        "event": "capture_failed",
                        "session_id": session_id,
                        "room_id": room_id,
                        "result": "error",
                    },
                )
                raise CaptureError(
                    f"Camera {room_id} failed on shot {shot}/{shots}: {exc} "
                    f"(camera status={health.status.value})"
                ) from exc
            capture.files.append(result["file"])

        self._manifests.setdefault(session_id, {})[room_id] = capture
        logger.info(
            "Room captured",
            extra={
                "event": "room_captured",
                "session_id": session_id,
                "room_id": room_id,
                "result": "ok",
            },
        )
        return capture

    def frames(self, session_id: str) -> list[dict[str, str]]:
        """Flatten a session's captures into individually addressable frames.

        Each frame gets a stable `frame_id` (`<room>-<n>`) so downstream
        selection/printing can reference a specific shot.
        """
        out: list[dict[str, str]] = []
        for room_id, rc in self._manifests.get(session_id, {}).items():
            for n, file in enumerate(rc.files, start=1):
                out.append({"frame_id": f"{room_id}-{n}", "room_id": room_id, "file": file})
        return out

    def manifest(self, session_id: str) -> dict[str, dict[str, object]]:
        """Return the per-room capture summary for a session (empty dict if none)."""
        rooms = self._manifests.get(session_id, {})
        return {
            room_id: {
                "files": rc.files,
                "shots_requested": rc.shots_requested,
                "shots_captured": len(rc.files),
            }
            for room_id, rc in rooms.items()
        }
