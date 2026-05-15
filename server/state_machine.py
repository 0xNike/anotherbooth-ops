from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum

from server.config import AppConfig

logger = logging.getLogger(__name__)


class SessionState(str, Enum):
    IDLE = "IDLE"
    ACTIVE_R2 = "ACTIVE_R2"
    ACTIVE_R3 = "ACTIVE_R3"
    ACTIVE_R4 = "ACTIVE_R4"
    ACTIVE_R5 = "ACTIVE_R5"
    SELECTING = "SELECTING"
    PRINTING = "PRINTING"


ROOM_SEQUENCE = [SessionState.ACTIVE_R2, SessionState.ACTIVE_R3, SessionState.ACTIVE_R4, SessionState.ACTIVE_R5]


@dataclass
class SessionContext:
    session_id: str
    current_state: SessionState = SessionState.IDLE


class SessionStateMachine:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.ctx = SessionContext(session_id="")
        self._lock = asyncio.Lock()

    async def start_session(self, session_id: str) -> SessionContext:
        async with self._lock:
            if self.ctx.current_state != SessionState.IDLE:
                raise RuntimeError("Cannot start session while another is active")
            self.ctx = SessionContext(session_id=session_id, current_state=SessionState.ACTIVE_R2)
            logger.info("Session started", extra={"event": "session_start", "session_id": session_id, "result": "ok"})
            return self.ctx

    async def advance(self) -> SessionState:
        async with self._lock:
            state = self.ctx.current_state
            if state in ROOM_SEQUENCE:
                idx = ROOM_SEQUENCE.index(state)
                self.ctx.current_state = ROOM_SEQUENCE[idx + 1] if idx < len(ROOM_SEQUENCE) - 1 else SessionState.SELECTING
            elif state == SessionState.SELECTING:
                self.ctx.current_state = SessionState.PRINTING
            elif state == SessionState.PRINTING:
                self.ctx.current_state = SessionState.IDLE
            else:
                self.ctx.current_state = SessionState.IDLE

            logger.info(
                "Session advanced",
                extra={
                    "event": "session_advance",
                    "session_id": self.ctx.session_id,
                    "result": "ok",
                    "room_id": self.ctx.current_state.value,
                },
            )
            return self.ctx.current_state
