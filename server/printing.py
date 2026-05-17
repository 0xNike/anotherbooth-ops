from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from enum import Enum

from server.capture import CaptureService

logger = logging.getLogger(__name__)


class PrinterStatus(str, Enum):
    ONLINE = "ONLINE"
    BUSY = "BUSY"
    OFFLINE = "OFFLINE"


@dataclass
class PrinterHealth:
    status: PrinterStatus
    last_error: str | None = None


@dataclass
class PrintJob:
    job_id: str
    paper_size: str
    frame_ids: list[str]
    status: str  # "PRINTED" on success


class SimulatedPrinterAdapter:
    """Stand-in for the DNP DS-RX1HS dye-sub printer; no hardware required.

    Mirrors the camera adapter pattern so a real printer adapter can drop in
    later behind the same `health_check` / `submit_job` interface.
    """

    def __init__(self, name: str, paper_size: str) -> None:
        self.name = name
        self.paper_size = paper_size
        self._health = PrinterHealth(status=PrinterStatus.ONLINE)

    async def health_check(self) -> PrinterHealth:
        return self._health

    async def submit_job(self, frame_ids: list[str]) -> PrintJob:
        await asyncio.sleep(0.05)
        return PrintJob(
            job_id=uuid.uuid4().hex[:8],
            paper_size=self.paper_size,
            frame_ids=list(frame_ids),
            status="PRINTED",
        )


class PrintError(RuntimeError):
    """Raised when a selection or print job is invalid."""


class PrintService:
    """Tracks per-session photo selection and drives the printer adapter."""

    def __init__(self, capture: CaptureService, printer: SimulatedPrinterAdapter) -> None:
        self.capture = capture
        self.printer = printer
        self._selections: dict[str, list[str]] = {}
        self._jobs: dict[str, PrintJob] = {}

    def select(self, session_id: str, frame_ids: list[str]) -> list[str]:
        """Record the frames chosen for printing, validated against the manifest."""
        if not frame_ids:
            raise PrintError("Selection is empty")
        available = {f["frame_id"] for f in self.capture.frames(session_id)}
        unknown = [fid for fid in frame_ids if fid not in available]
        if unknown:
            raise PrintError(f"Unknown frame ids: {', '.join(unknown)}")
        chosen = list(dict.fromkeys(frame_ids))  # dedupe, preserve order
        self._selections[session_id] = chosen
        logger.info(
            "Frames selected",
            extra={"event": "frames_selected", "session_id": session_id, "result": "ok"},
        )
        return chosen

    def selection(self, session_id: str) -> list[str]:
        return self._selections.get(session_id, [])

    async def print_session(self, session_id: str) -> PrintJob:
        """Submit the selected frames to the printer; raise PrintError if none selected."""
        chosen = self._selections.get(session_id)
        if not chosen:
            raise PrintError("No photos selected for printing")
        job = await self.printer.submit_job(chosen)
        self._jobs[session_id] = job
        logger.info(
            "Print job submitted",
            extra={"event": "print_submitted", "session_id": session_id, "result": "ok"},
        )
        return job

    def job(self, session_id: str) -> PrintJob | None:
        return self._jobs.get(session_id)
