from fastapi.testclient import TestClient

from village_insight.api.app import app


def test_liveness() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
