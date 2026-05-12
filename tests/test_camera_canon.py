from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from server.camera import CameraStatus
from server.camera_canon import CanonCCAPIAdapter, CCAPIError


def _make_client(handler):
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, base_url="http://camera")


@pytest.mark.asyncio
async def test_discover_picks_highest_version():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ccapi/"
        return httpx.Response(200, json={"ver100": [], "ver110": [], "ver130": []})

    async with CanonCCAPIAdapter(room_id="R2", host="camera", client=_make_client(handler)) as cam:
        assert await cam._discover_version() == "ver130"


@pytest.mark.asyncio
async def test_trigger_shutter_success_returns_ok_shape():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/ccapi/":
            return httpx.Response(200, json={"ver100": []})
        if request.url.path == "/ccapi/ver100/shooting/control/shutterbutton":
            return httpx.Response(200, json={"result": "ok"})
        return httpx.Response(404)

    async with CanonCCAPIAdapter(room_id="R2", host="camera", client=_make_client(handler)) as cam:
        result = await cam.trigger_shutter()

    assert result == {"result": "ok", "room_id": "R2"}
    assert "POST /ccapi/ver100/shooting/control/shutterbutton" in calls
    assert (await cam.health_check()).status == CameraStatus.ONLINE


@pytest.mark.asyncio
async def test_trigger_shutter_failure_degrades_then_offlines():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ccapi/":
            return httpx.Response(200, json={"ver100": []})
        return httpx.Response(500, json={"message": "boom"})

    async with CanonCCAPIAdapter(room_id="R2", host="camera", client=_make_client(handler)) as cam:
        with pytest.raises(CCAPIError):
            await cam.trigger_shutter()
        assert cam._health.status == CameraStatus.DEGRADED

        with pytest.raises(CCAPIError):
            await cam.trigger_shutter()
        with pytest.raises(CCAPIError):
            await cam.trigger_shutter()
        assert cam._health.status == CameraStatus.OFFLINE
        assert cam._health.consecutive_failures == 3


@pytest.mark.asyncio
async def test_download_latest_writes_file(tmp_path: Path):
    bytes_payload = b"\xff\xd8\xff\xe0fake-jpeg"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/ccapi/":
            return httpx.Response(200, json={"ver100": []})
        if path == "/ccapi/ver100/contents/sd":
            return httpx.Response(
                200,
                json={"url": ["http://camera/ccapi/ver100/contents/sd/100CANON"]},
            )
        if path == "/ccapi/ver100/contents/sd/100CANON":
            return httpx.Response(
                200,
                json={
                    "url": [
                        "http://camera/ccapi/ver100/contents/sd/100CANON/IMG_0001.JPG",
                        "http://camera/ccapi/ver100/contents/sd/100CANON/IMG_0002.JPG",
                    ]
                },
            )
        if path.endswith("/IMG_0002.JPG"):
            return httpx.Response(200, content=bytes_payload)
        return httpx.Response(404)

    async with CanonCCAPIAdapter(
        room_id="R2",
        host="camera",
        capture_dir=tmp_path,
        client=_make_client(handler),
    ) as cam:
        result = await cam.download_latest()

    saved = Path(result["file"])
    assert saved.exists()
    assert saved.read_bytes() == bytes_payload
    assert saved.name == "IMG_0002.JPG"
