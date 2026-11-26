"""GGUF header parsing and context-aware LLM fit."""
import json
import struct
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import gguf_meta
from backend.app.fit import GB, rate_llm
from backend.app.main import app


def _string(text):
    raw = text.encode()
    return struct.pack("<Q", len(raw)) + raw


def gguf_header(arch="qwen3", **values):
    """A GGUF v3 header: a string, ints, and a tokenizer string array the parser has to skip."""
    pairs = [("general.architecture", 8, _string(arch))]
    for key, value in values.items():
        key = arch + "." + key.replace("__", ".")
        if isinstance(value, list):
            pairs.append((key, 9, struct.pack("<IQ", 4, len(value)) + b"".join(struct.pack("<I", v) for v in value)))
        else:
            pairs.append((key, 4, struct.pack("<I", value)))
    pairs.append(("tokenizer.ggml.tokens", 9, struct.pack("<IQ", 8, 3) + _string("a") + _string("b") + _string("c")))
    body = b"".join(_string(k) + struct.pack("<I", t) + v for k, t, v in pairs)
    return b"GGUF" + struct.pack("<IQQ", 3, 0, len(pairs)) + body


QWEN3_8B = dict(block_count=36, context_length=40960, embedding_length=4096, attention__head_count=32,
                attention__head_count_kv=8, attention__key_length=128, attention__value_length=128)


def test_parse_header_reads_the_shape_and_kv_size():
    meta = gguf_meta.parse_header(gguf_header(**QWEN3_8B))
    assert meta["architecture"] == "qwen3" and meta["block_count"] == 36
    shape = gguf_meta.summary(meta)
    assert shape["kv_bytes_per_token"] == 36 * 8 * (128 + 128) * 2  # 144 KiB: f16 K and V, all layers
    assert shape["context_length"] == 40960


def test_parse_header_survives_truncation_and_junk():
    data = gguf_header(**QWEN3_8B)
    assert gguf_meta.parse_header(data[:-10])["block_count"] == 36  # cut inside the tokenizer array
    assert gguf_meta.parse_header(b"not a gguf") is None
    assert gguf_meta.parse_header(data[:12]) is None


def test_per_layer_kv_heads_and_missing_key_length():
    meta = gguf_meta.parse_header(gguf_header("x", block_count=2, embedding_length=1024, attention__head_count=8,
                                              attention__head_count_kv=[2, 0]))
    assert gguf_meta.kv_bytes_per_token(meta) == 2 * (128 + 128) * 2  # head size 1024/8; layer 2 has no attention


def test_longer_context_needs_more_memory():
    hw = {"gpu": "RX 9070 XT", "vram_bytes": 16 * GB, "ram_bytes": 64 * GB}
    kv = 147456
    assert rate_llm("m-Q8_0.gguf", int(8.7 * GB), hw, "llama.cpp", 8192, kv)["level"] == "good"
    long = rate_llm("m-Q8_0.gguf", int(8.7 * GB), hw, "llama.cpp", 65536, kv)
    assert long["level"] == "ok" and "64K context" in long["note"]
    assert "a 32K context" in rate_llm("m-Q4_K_M.gguf", int(5 * GB), hw, "llama.cpp", 32768, kv)["note"]


def test_inspect_uses_the_header_and_chosen_context(tmp_path, monkeypatch):
    from backend.app import main
    monkeypatch.setattr(main, "DB", tmp_path / "db.sqlite3")
    monkeypatch.setattr(main, "TOKEN", "t")
    monkeypatch.setattr(gguf_meta, "fetch_range", lambda url, size=None: gguf_header(**QWEN3_8B))
    client, headers = TestClient(app), {"X-ModelHarbor-Token": "t"}
    client.post("/api/agents/heartbeat", headers=headers, json={
        "device": "pc", "comfyui_count": 0, "llamacpp_count": 0,
        "hardware": {"gpu": "RX 9070 XT", "vendor": "amd", "vram_bytes": 16 * GB, "ram_bytes": 64 * GB}})
    files = [{"path": f"Qwen3-8B-{q}.gguf", "name": f"Qwen3-8B-{q}.gguf", "size_bytes": int(s * GB), "sha256": "a" * 64}
             for q, s in (("Q4_K_M", 4.7), ("Q6_K", 6.3), ("Q8_0", 8.1))]
    meta = {"id": "Qwen/Qwen3-8B-GGUF", "pipeline_tag": "text-generation", "tags": ["gguf"], "cardData": {}}

    def inspect(context):
        with patch("backend.app.hf_inspect.fetch", return_value=json.dumps(meta).encode()), \
                patch("backend.app.hf_inspect.list_model_files", return_value={"files": [dict(f) for f in files]}):
            return client.get("/api/huggingface/inspect", headers=headers,
                              params={"url": "Qwen/Qwen3-8B-GGUF", "agent": "pc", "context": context}).json()
    short, long = inspect(8192), inspect(65536)
    assert short["gguf"]["kv_bytes_per_token"] == 147456 and short["context"] == 8192
    assert short["recommended"]["llamacpp"] == "Qwen3-8B-Q8_0.gguf"
    # 64K of context is 9 GB of KV cache: only Q4_K_M still fits in 16 GB of VRAM with it.
    assert long["recommended"]["llamacpp"] == "Qwen3-8B-Q4_K_M.gguf"
    assert client.get("/api/huggingface/inspect", headers=headers,
                      params={"url": "x/y", "context": 10}).status_code == 422
