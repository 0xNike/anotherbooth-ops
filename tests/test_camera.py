import pytest

from server.camera import CameraStatus, CircuitBreaker, SimulatedCameraAdapter


def test_circuit_breaker_opens_after_threshold():
    breaker = CircuitBreaker(threshold=3, window_seconds=30)
    opened, count = breaker.mark_failure()
    assert opened is False
    assert count == 1
    opened, count = breaker.mark_failure()
    assert opened is False
    assert count == 2
    opened, count = breaker.mark_failure()
    assert opened is True
    assert count == 3


@pytest.mark.asyncio
async def test_simulated_camera_success_health_online():
    camera = SimulatedCameraAdapter("R2")
    result = await camera.trigger_shutter()
    health = await camera.health_check()
    assert result["result"] == "ok"
    assert health.status == CameraStatus.ONLINE
