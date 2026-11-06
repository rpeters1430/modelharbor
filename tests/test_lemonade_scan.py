import hashlib
from agent.lemonade_scan import collect, system_info

class FakeResponse:
    status_code = 200
    def raise_for_status(self):
        pass
    def json(self):
        return {"object": "list", "data": [
            {"id": "user.o-r-model-Q8_0", "recipe": "llamacpp", "size": 4.5, "downloaded": True,
             "checkpoint": "o/r:model-Q8_0.gguf", "checkpoints": {"main": "o/r:model-Q8_0.gguf"}, "update_available": True},
            {"id": "Qwen3-0.6B-GGUF", "recipe": "llamacpp", "size": 0.38, "downloaded": False},
            {"id": "fireworks.kimi", "recipe": "cloud"}]}

class FakeSession:
    def get(self, url, headers, timeout):
        assert url == "http://localhost:13305/api/v1/models"
        assert headers == {"Authorization": "Bearer secret"}
        return FakeResponse()

def test_collect_lists_only_downloaded_local_models(monkeypatch):
    monkeypatch.setenv("LEMONADE_API_KEY", "secret")
    assert collect(FakeSession(), "http://localhost:13305/", "pc:lemonade") == [{
        "device": "pc:lemonade", "path": "lemonade://user.o-r-model-Q8_0", "name": "user.o-r-model-Q8_0",
        "kind": "lemonade/llamacpp", "size_bytes": int(4.5 * 2**30), "modified_ns": 0, "sha256": None,
        "checkpoint": "o/r:model-Q8_0.gguf", "update_available": True,
        "file_id": "ckpt:" + hashlib.sha256(b"o/r:model-q8_0.gguf").hexdigest()[:40]}]


SYSTEM_INFO = {  # shape of a real Lemonade 10 response
    "Physical Memory": "128.00 GB",
    "devices": {"amd_gpu": [{"available": True, "integrated": True, "name": "AMD Radeon(TM) Graphics", "vram_gb": 0.5},
                            {"available": True, "integrated": False, "name": "AMD Radeon RX 9070 XT", "vram_gb": 17179869183.999023}],
                "nvidia_gpu": [{"available": False, "name": ""}]},
    "model_storage": {"free_bytes": 584570695680}}

class InfoSession:
    def get(self, url, headers, timeout):
        assert url == "http://192.168.8.143:13305/api/v1/system-info"
        response = FakeResponse()
        response.json = lambda: SYSTEM_INFO
        return response

def test_system_info_reports_the_lemonade_pcs_discrete_gpu():
    assert system_info(InfoSession(), "http://192.168.8.143:13305") == {
        "host": "192.168.8.143", "ram_bytes": 128 * 2**30, "free_bytes": 584570695680,
        "gpu": "AMD Radeon RX 9070 XT", "vendor": "amd", "vram_bytes": 16 * 2**30}
