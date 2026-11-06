import importlib.util
from pathlib import Path

def test_agent_config_example():
    import json
    cfg=json.loads(Path("agent/config.example.json").read_text())
    assert cfg["sync_interval_seconds"] >= 300
    assert cfg["ollama_enabled"] is True
    assert cfg["lemonade_enabled"] is True and cfg["lemonade_url"].startswith("http://127.0.0.1:")

def test_heartbeat_schema():
    from backend.app.main import Heartbeat
    h=Heartbeat(device="pc",comfyui_count=2,ollama_count=None)
    assert h.device=="pc"
