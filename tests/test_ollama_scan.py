from agent.ollama_scan import collect

class FakeResponse:
    def raise_for_status(self):
        pass
    def json(self):
        return {"models":[{"name":"qwen:14b","size":1234,"digest":"abc",
                 "details":{"family":"qwen"}}]}

class FakeSession:
    def get(self, url, timeout):
        assert url == "http://localhost:11434/api/tags"
        return FakeResponse()

def test_collect():
    models = collect(FakeSession(), "http://localhost:11434/", "test:ollama")
    assert models == [{"device":"test:ollama","path":"ollama://qwen:14b",
        "name":"qwen:14b","kind":"ollama/qwen","size_bytes":1234,"modified_ns":0}]
