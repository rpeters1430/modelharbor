import os
os.environ["MODELHARBOR_AGENT_TOKEN"] = "test-token"
from fastapi.testclient import TestClient
from backend.app.main import app

def test_health():
    assert TestClient(app).get("/api/health").json() == {"status": "ok"}

def test_auth_required():
    assert TestClient(app).get("/api/models").status_code == 401
