from fastapi.testclient import TestClient


def test_health():
    from backend.app.main import app
    c = TestClient(app)
    r = c.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
