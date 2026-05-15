from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum

import httpx


class CameraStatus(str, Enum):
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"


@dataclass
class CameraHealth:
    status: CameraStatus
    last_error: str | None = None
    consecutive_failures: int = 0


class CircuitBreaker:
    def __init__(self, threshold: int = 3, window_seconds: int = 30) -> None:
        self.threshold = threshold
        self.window_seconds = window_seconds
        self._failures: list[float] = []

    def mark_success(self) -> None:
        self._failures.clear()

    def mark_failure(self) -> tuple[bool, int]:
        now = time.time()
        self._failures.append(now)
        self._failures = [f for f in self._failures if now - f <= self.window_seconds]
        return len(self._failures) >= self.threshold, len(self._failures)


class BaseCameraAdapter:
    def __init__(self, room_id: str) -> None:
        self.room_id = room_id
        self._health = CameraHealth(status=CameraStatus.ONLINE)
        self._breaker = CircuitBreaker()

    async def health_check(self) -> CameraHealth:
        return self._health

    def _record_success(self) -> None:
        self._breaker.mark_success()
        self._health = CameraHealth(status=CameraStatus.ONLINE)

    def _record_failure(self, error: str) -> CameraHealth:
        opened, count = self._breaker.mark_failure()
        self._health = CameraHealth(
            status=CameraStatus.OFFLINE if opened else CameraStatus.DEGRADED,
            last_error=error,
            consecutive_failures=count,
        )
        return self._health


class SimulatedCameraAdapter(BaseCameraAdapter):
    async def trigger_shutter(self) -> dict[str, str]:
        await asyncio.sleep(0.05)
        self._record_success()
        return {"result": "ok", "room_id": self.room_id, "mode": "simulated"}

    async def download_latest(self) -> dict[str, str]:
        await asyncio.sleep(0.05)
        self._record_success()
        return {"result": "ok", "file": f"{self.room_id}-simulated.jpg", "mode": "simulated"}


class CanonCcapiAdapter(BaseCameraAdapter):
    def __init__(self, room_id: str, camera_ip: str, timeout_seconds: float = 5.0) -> None:
        super().__init__(room_id)
        self.camera_ip = camera_ip
        self.timeout_seconds = timeout_seconds
        self._base_url = f"http://{camera_ip}"

    async def health_check(self) -> CameraHealth:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(f"{self._base_url}/ccapi")
                response.raise_for_status()
            self._record_success()
        except Exception as exc:  # network/hardware boundary
            self._record_failure(str(exc))
        return self._health

    async def trigger_shutter(self) -> dict[str, str]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/ccapi/shooting/control/shutterbutton",
                    json={"af": True, "action": "full_press"},
                )
                response.raise_for_status()
            self._record_success()
            return {"result": "ok", "room_id": self.room_id, "mode": "ccapi"}
        except Exception as exc:  # network/hardware boundary
            health = self._record_failure(str(exc))
            return {
                "result": "error",
                "room_id": self.room_id,
                "mode": "ccapi",
                "status": health.status.value,
                "error": health.last_error or "unknown",
            }

    async def download_latest(self) -> dict[str, str]:
        # Full media listing/download flow to be implemented with hardware validation.
        self._record_success()
        return {"result": "todo", "room_id": self.room_id, "mode": "ccapi"}
