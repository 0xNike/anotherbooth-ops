from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum


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

    def mark_failure(self) -> bool:
        now = time.time()
        self._failures.append(now)
        self._failures = [f for f in self._failures if now - f <= self.window_seconds]
        return len(self._failures) >= self.threshold


class SimulatedCameraAdapter:
    def __init__(self, room_id: str) -> None:
        self.room_id = room_id
        self._health = CameraHealth(status=CameraStatus.ONLINE)
        self._breaker = CircuitBreaker()

    async def health_check(self) -> CameraHealth:
        return self._health

    async def trigger_shutter(self) -> dict[str, str]:
        await asyncio.sleep(0.05)
        self._breaker.mark_success()
        self._health = CameraHealth(status=CameraStatus.ONLINE)
        return {"result": "ok", "room_id": self.room_id}

    async def download_latest(self) -> dict[str, str]:
        await asyncio.sleep(0.05)
        return {"result": "ok", "file": f"{self.room_id}-simulated.jpg"}

    def mark_failure(self, error: str) -> CameraHealth:
        opened = self._breaker.mark_failure()
        if opened:
            self._health = CameraHealth(
                status=CameraStatus.OFFLINE,
                last_error=error,
                consecutive_failures=self._breaker.threshold,
            )
        else:
            self._health = CameraHealth(status=CameraStatus.DEGRADED, last_error=error, consecutive_failures=1)
        return self._health
