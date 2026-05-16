from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from server.camera import CameraHealth, CameraStatus, CircuitBreaker

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8080
DEFAULT_TIMEOUT = 10.0
CAPTURE_ROOT = Path("sessions/captures")


class CCAPIError(RuntimeError):
    pass


class CanonCCAPIAdapter:
    """Canon CCAPI adapter with the same async surface as SimulatedCameraAdapter.

    Swap this in for SimulatedCameraAdapter in server/main.py when real hardware
    is connected. Tests can inject a custom httpx.AsyncClient (e.g. backed by
    MockTransport) via the `client` kwarg.
    """

    def __init__(
        self,
        room_id: str,
        host: str,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
        capture_dir: Path | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.room_id = room_id
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self._timeout = timeout
        self._capture_dir = (capture_dir or CAPTURE_ROOT) / room_id
        self._api_version: str | None = None
        self._health = CameraHealth(status=CameraStatus.ONLINE)
        self._breaker = CircuitBreaker()
        self._lock = asyncio.Lock()
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "CanonCCAPIAdapter":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def _discover_version(self) -> str:
        if self._api_version:
            return self._api_version
        client = await self._get_client()
        resp = await client.get(f"{self.base_url}/ccapi/")
        resp.raise_for_status()
        data = resp.json()
        versions = [k for k in data.keys() if k.startswith("ver")]
        if not versions:
            raise CCAPIError(f"No CCAPI versions advertised by {self.host}")
        self._api_version = sorted(versions)[-1]
        return self._api_version

    async def _api(self) -> str:
        return f"{self.base_url}/ccapi/{await self._discover_version()}"

    def _record_success(self) -> None:
        self._breaker.mark_success()
        self._health = CameraHealth(status=CameraStatus.ONLINE)

    def _record_failure(self, error: str) -> CameraHealth:
        opened = self._breaker.mark_failure()
        if opened:
            self._health = CameraHealth(
                status=CameraStatus.OFFLINE,
                last_error=error,
                consecutive_failures=self._breaker.threshold,
            )
        else:
            self._health = CameraHealth(
                status=CameraStatus.DEGRADED,
                last_error=error,
                consecutive_failures=1,
            )
        return self._health

    async def health_check(self) -> CameraHealth:
        try:
            client = await self._get_client()
            resp = await client.get(f"{self.base_url}/ccapi/", timeout=3.0)
            resp.raise_for_status()
            self._record_success()
        except Exception as exc:
            self._record_failure(str(exc))
            logger.warning(
                "Camera health check failed",
                extra={"event": "camera_health", "room_id": self.room_id, "result": "warning"},
            )
        return self._health

    async def trigger_shutter(self) -> dict[str, str]:
        async with self._lock:
            try:
                api = await self._api()
                client = await self._get_client()
                resp = await client.post(
                    f"{api}/shooting/control/shutterbutton",
                    json={"af": True},
                )
                resp.raise_for_status()
                self._record_success()
                logger.info(
                    "Shutter fired",
                    extra={"event": "shutter_fire", "room_id": self.room_id, "result": "ok"},
                )
                return {"result": "ok", "room_id": self.room_id}
            except Exception as exc:
                self._record_failure(str(exc))
                logger.error(
                    "Shutter trigger failed",
                    extra={"event": "shutter_fire", "room_id": self.room_id, "result": "error"},
                )
                raise CCAPIError(f"shutter failed: {exc}") from exc

    async def _list_latest_file_url(self) -> str:
        """Walk /contents/sd → 100CANON-style folder → last file URL."""
        api = await self._api()
        client = await self._get_client()

        sd_resp = await client.get(f"{api}/contents/sd")
        sd_resp.raise_for_status()
        folders = self._extract_urls(sd_resp.json())
        if not folders:
            raise CCAPIError("No folders on SD card")

        folder_resp = await client.get(folders[-1])
        folder_resp.raise_for_status()
        files = self._extract_urls(folder_resp.json())
        if not files:
            raise CCAPIError("No files in latest SD folder")
        return files[-1]

    @staticmethod
    def _extract_urls(payload: Any) -> list[str]:
        if isinstance(payload, dict):
            for key in ("url", "path", "contentsnumber"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [str(v) for v in value]
            for value in payload.values():
                if isinstance(value, list):
                    return [str(v) for v in value]
        if isinstance(payload, list):
            return [str(v) for v in payload]
        return []

    async def download_latest(self) -> dict[str, str]:
        try:
            client = await self._get_client()
            file_url = await self._list_latest_file_url()
            resp = await client.get(file_url)
            resp.raise_for_status()
            self._capture_dir.mkdir(parents=True, exist_ok=True)
            filename = file_url.rsplit("/", 1)[-1] or "capture.bin"
            dest = self._capture_dir / filename
            dest.write_bytes(resp.content)
            self._record_success()
            logger.info(
                "Capture downloaded",
                extra={"event": "capture_download", "room_id": self.room_id, "result": "ok"},
            )
            return {"result": "ok", "file": str(dest)}
        except Exception as exc:
            self._record_failure(str(exc))
            raise CCAPIError(f"download failed: {exc}") from exc


async def _smoke_test(host: str, port: int, shots: int, gap: float, download: bool) -> int:
    import time

    adapter = CanonCCAPIAdapter(room_id="smoke", host=host, port=port)
    fails = 0
    try:
        version = await adapter._discover_version()
        print(f"[ok] CCAPI {host}:{port} version={version}")

        for i in range(1, shots + 1):
            t0 = time.perf_counter()
            try:
                await adapter.trigger_shutter()
                dt = (time.perf_counter() - t0) * 1000
                print(f"[ok] shot {i}/{shots} trigger={dt:.0f}ms")
            except CCAPIError as exc:
                fails += 1
                print(f"[FAIL] shot {i}/{shots}: {exc}")
            if i < shots:
                await asyncio.sleep(gap)

        if download:
            try:
                result = await adapter.download_latest()
                print(f"[ok] latest downloaded -> {result['file']}")
            except CCAPIError as exc:
                fails += 1
                print(f"[FAIL] download: {exc}")
    finally:
        await adapter.close()

    print(f"=== summary: {shots - fails}/{shots} shots ok, health={adapter._health.status.value} ===")
    return 0 if fails == 0 else 1


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Canon CCAPI burst smoke test")
    parser.add_argument("host", help="Camera IP (CCAPI must be enabled)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--shots", type=int, default=3)
    parser.add_argument("--gap", type=float, default=5.0, help="Seconds between shots")
    parser.add_argument("--no-download", action="store_true", help="Skip downloading latest file")
    args = parser.parse_args()
    return asyncio.run(
        _smoke_test(args.host, args.port, args.shots, args.gap, download=not args.no_download)
    )


if __name__ == "__main__":
    raise SystemExit(main())
