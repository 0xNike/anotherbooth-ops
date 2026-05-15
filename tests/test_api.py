from fastapi.testclient import TestClient

from server.main import app


client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_admin_status():
    response = client.get("/admin/status")
    assert response.status_code == 200
    payload = response.json()
    assert "camera_health" in payload
    assert "R2" in payload["camera_health"]
