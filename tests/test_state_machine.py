import pytest

from server.config import AppConfig
from server.state_machine import SessionState, SessionStateMachine


@pytest.mark.asyncio
async def test_happy_path_progression():
    fsm = SessionStateMachine(AppConfig())
    await fsm.start_session("abc123")
    assert fsm.ctx.current_state == SessionState.ACTIVE_R2
    await fsm.advance()
    await fsm.advance()
    await fsm.advance()
    await fsm.advance()
    assert fsm.ctx.current_state == SessionState.SELECTING


@pytest.mark.asyncio
async def test_cannot_start_when_active():
    fsm = SessionStateMachine(AppConfig())
    await fsm.start_session("abc123")
    with pytest.raises(RuntimeError):
        await fsm.start_session("def456")
