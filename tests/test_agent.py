import importlib.util
from pathlib import Path

def test_agent_config_example():
    import json
    cfg=json.loads(Path("agent/config.example.json").read_text())
    assert cfg["sync_interval_seconds"] >= 300
    assert cfg["ollama_enabled"] is True

def test_heartbeat_schema():
    from backend.app.main import Heartbeat
    h=Heartbeat(device="pc",comfyui_count=2,ollama_count=None)
    assert h.device=="pc"
