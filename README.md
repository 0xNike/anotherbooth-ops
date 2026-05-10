# anotherbooth-ops

Initial backend scaffolding for Another Booth's centralized operations server.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn server.main:app --reload --host 0.0.0.0 --port 8000
```

## Current scope

- FastAPI app + health route
- Session state machine skeleton for room progression
- WebSocket endpoint for tablet client ack testing
- Config loading from `config.yaml`
- Structured JSON logging setup
- Retention worker placeholder for scheduled session cleanup

## Notes

- This project currently targets `dev_single_cam` mode.
- Rooms R3-R5 are configured as simulated capture by default.
- No production camera/print integrations are implemented yet.
