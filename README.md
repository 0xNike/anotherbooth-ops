# anotherbooth-ops

Initial backend scaffolding for Another Booth's centralized operations server.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt          # runtime deps
pip install -r requirements-dev.txt      # test deps (pytest, pytest-asyncio)
uvicorn server.main:app --reload --host 0.0.0.0 --port 8000
```

## Verification gates

Proceed to next feature work only when **all four** of these pass:

1. `git status` is clean.
2. `python -m compileall server tests` — bytecode-compile everything (cheap syntax gate).
3. `PYTHONPATH=. pytest -q` — unit/integration tests green.
4. Running app responds 200 on both `GET /health` and `GET /admin/status`.

`npm run build` is a placeholder (`package.json`) and currently proves nothing — treat it as **informational only** until a real frontend bundle/lint/typecheck exists behind it.

## Current scope

- FastAPI app:
  - `GET /health`, `GET /admin/status` (per-room `camera_health` + live session state)
  - `POST /admin/session/start` / `POST /admin/session/advance` — drive the room state machine
  - `POST /admin/session/capture` — capture the current room via its camera adapter
  - `GET /admin/session/{id}/manifest` — captured files per room for a session
- Session state machine skeleton for room progression (`IDLE → R2…R5 → SELECTING → PRINTING`)
- `CaptureService` (`server/capture.py`) — ties session state → camera adapter → per-session file manifest; shoots `timing.shots_per_room` frames per room
- WebSocket endpoint for tablet client ack testing
- Config loading from `config.yaml`
- Structured JSON logging setup
- Retention worker placeholder for scheduled session cleanup
- `SimulatedCameraAdapter` for development without hardware
- `CanonCCAPIAdapter` (`server/camera_canon.py`) — production-ready Canon Wi-Fi adapter, drop-in compatible with the simulator interface. CLI smoke test: `python -m server.camera_canon HOST --shots N`.

## Hardware compatibility

Target hardware for production booths: **Canon EOS bodies that support CCAPI** (Canon Camera Control API). Verified compatible models include R5, R6/R6 Mark II, R7, R8, R10, R50, R3, R5C. The `CanonCCAPIAdapter` is built against this API.

**Prerequisite:** CCAPI is gated behind Canon Developer Resources registration (free; the NDA covers redistribution of the spec, not commercial use of the camera). Apply at Canon's developer portal and enable CCAPI on each body via the camera menu before the adapter can connect.

### Models known NOT to work

- **Canon EOS R100** — does not expose CCAPI; no `CCAPI` toggle in its menu and no HTTP service on the camera. gPhoto2 detects it over PTP/IP but the camera rejects PTP capture commands (Canon's EOS Utility uses an authenticated handshake that gphoto2 cannot impersonate). Driving the R100 from this codebase over Wi-Fi is not feasible. USB-tether via gphoto2/EDSDK is technically possible but requires a per-room host machine — out of scope for this architecture. **Do not rent R100s for this project.**

## Swapping the simulator for real cameras

In `server/main.py`, replace:

```python
from server.camera import SimulatedCameraAdapter
cameras = {room_id: SimulatedCameraAdapter(room_id) for room_id in config.rooms}
```

with:

```python
from server.camera_canon import CanonCCAPIAdapter
cameras = {
    room_id: CanonCCAPIAdapter(room_id, host=room.camera_ip)
    for room_id, room in config.rooms.items()
}
```

The adapter shape is identical (`health_check`, `trigger_shutter`, `download_latest`), so `/admin/status` and the rest of the app are unchanged.

## Notes

- This project currently targets `dev_single_cam` mode.
- All adapter usage is simulated until CCAPI access is approved and a compatible body (e.g. R50) is validated against `python -m server.camera_canon`.
