from agent.lemonade_scan import collect

class FakeResponse:
    status_code = 200
    def raise_for_status(self):
        pass
    def json(self):
        return {"object": "list", "data": [
            {"id": "user.o-r-model-Q8_0", "recipe": "llamacpp", "size": 4.5, "downloaded": True},
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
        "kind": "lemonade/llamacpp", "size_bytes": 4_500_000_000, "modified_ns": 0, "sha256": None}]
