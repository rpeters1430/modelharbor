"""Recommended files from an inspected link follow the PC's hardware and the chosen LLM server."""
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import fit
from backend.app.fit import GB, annotate_listing, gguf_runtime, mark_recommended, recommend_file
from backend.app.hf_inspect import recommend_primary
from backend.app.main import app

AMD = {"gpu": "Radeon RX 9070 XT", "vendor": "amd", "vram_bytes": 16 * GB, "ram_bytes": 64 * GB}
QUANTS = (("Q2_K", 3.2), ("Q4_K_M", 4.9), ("Q6_K", 6.6), ("Q8_0", 8.5), ("F16", 16.1))


def gguf_files(model="Qwen3-8B"):
    return [{"path": f"{model}-{q}.gguf", "name": f"{model}-{q}.gguf", "size_bytes": int(s * GB), "sha256": "a" * 64}
            for q, s in QUANTS]


def test_comfyui_best_is_highest_precision_that_fits_not_smallest():
    files = [{"path": f"m_{q}.safetensors", "name": f"m_{q}.safetensors", "size_bytes": s * GB} for q, s in
             (("fp16", 12), ("fp8", 7))]
    for f in files:
        f["recommendation"] = recommend_file(f["name"], f["size_bytes"], AMD)
    mark_recommended(files, lambda f: "m")
    assert [f["name"] for f in files if f.get("best")] == ["m_fp16.safetensors"]


def test_llamacpp_and_ollama_are_rated_for_the_agent_pc():
    listing = annotate_listing({"files": gguf_files()}, AMD, runtime_hw={"llamacpp": AMD, "ollama": AMD})
    by_quant = {f["name"].split("-")[-1][:-5]: f for f in listing["files"]}
    assert by_quant["Q8_0"]["runtimes"]["llamacpp"]["best"] is True  # largest quant that fits with an 8K context
    assert by_quant["F16"]["runtimes"]["llamacpp"]["best"] is False
    assert by_quant["F16"]["runtimes"]["llamacpp"]["fit"]["level"] == "ok"
    small = {**AMD, "vram_bytes": 4 * GB, "ram_bytes": 8 * GB}
    listing = annotate_listing({"files": gguf_files()}, small, runtime_hw={"llamacpp": small})
    assert [f["name"] for f in listing["files"] if f["runtimes"]["llamacpp"]["best"]] == ["Qwen3-8B-Q2_K.gguf"]
    assert "llama.cpp PC" in listing["files"][-1]["runtimes"]["llamacpp"]["fit"]["note"]


def test_unquantized_gguf_loses_to_a_quant_that_fits():
    huge = {**AMD, "vram_bytes": 48 * GB}
    listing = annotate_listing({"files": gguf_files()}, huge, runtime_hw={"llamacpp": huge})
    assert [f["name"] for f in listing["files"] if f["runtimes"]["llamacpp"]["best"]] == ["Qwen3-8B-Q8_0.gguf"]


def test_gguf_runtime_says_which_server_suits_the_file():
    assert gguf_runtime("Qwen3-8B-Q4_K_M.gguf")["runtime"] == "ollama"
    assert gguf_runtime("Qwen3-8B-IQ4_XS.gguf")["runtime"] == "llamacpp"
    assert gguf_runtime("Qwen3-8B-UD-Q4_K_XL.gguf")["runtime"] == "llamacpp"
    assert gguf_runtime("Qwen3-8B-BF16.gguf")["runtime"] == "either"
    split = gguf_runtime("Q4_K_M/Qwen3-235B-Q4_K_M-00001-of-00003.gguf")
    assert split["runtime"] == "llamacpp" and split["ollama_ok"] is False
    assert gguf_runtime("mmproj-F16.gguf")["ollama_ok"] is False
    assert gguf_runtime("model.safetensors") is None
    # A mixture-of-experts model that spills out of VRAM runs better in llama.cpp; one that fits doesn't care.
    assert gguf_runtime("Qwen3-30B-A3B-Q4_K_M.gguf", 18 * GB, AMD)["runtime"] == "llamacpp"
    assert gguf_runtime("Qwen3-30B-A3B-Q4_K_M.gguf", 18 * GB, {**AMD, "vram_bytes": 32 * GB})["runtime"] == "ollama"


def test_recommend_primary_per_destination():
    files = gguf_files() + [{"path": "mmproj-F16.gguf", "name": "mmproj-F16.gguf", "size_bytes": GB}]
    lemonade = {**AMD, "vram_bytes": 8 * GB, "free_bytes": 500 * GB}
    annotate_listing({"files": files}, AMD, runtime_hw={"llamacpp": AMD, "ollama": AMD, "lemonade": lemonade})
    largest = max(files, key=lambda f: f["size_bytes"])
    data = recommend_primary({"files": files, "primary_file": largest, "specified_path": None})
    assert data["recommended"]["llamacpp"] == "Qwen3-8B-Q8_0.gguf"
    assert data["recommended"]["ollama"] == "Qwen3-8B-Q8_0.gguf"
    assert data["recommended"]["lemonade"] == "Qwen3-8B-Q4_K_M.gguf"  # the Lemonade PC has less VRAM
    # Unknown hardware: the usual Q4_K_M, never the biggest file.
    assert data["recommended"]["lmstudio"] == "Qwen3-8B-Q4_K_M.gguf"


def test_inspect_endpoint_recommends_for_the_selected_pc(tmp_path, monkeypatch):
    from backend.app import main
    monkeypatch.setattr(main, "DB", tmp_path / "db.sqlite3")
    monkeypatch.setattr(main, "TOKEN", "t")
    client, headers = TestClient(app), {"X-ModelHarbor-Token": "t"}
    assert client.post("/api/agents/heartbeat", headers=headers, json={
        "device": "pc", "comfyui_count": 0, "llamacpp_count": 0,
        "hardware": {"gpu": "RX 9070 XT", "vendor": "amd", "vram_bytes": 8 * GB, "ram_bytes": 32 * GB}}).status_code == 200
    meta = {"id": "Qwen/Qwen3-8B-GGUF", "tags": ["gguf"], "cardData": {}}
    with patch("backend.app.hf_inspect.fetch", return_value=json.dumps(meta).encode()), \
            patch("backend.app.hf_inspect.list_model_files", return_value={"files": gguf_files()}):
        data = client.get("/api/huggingface/inspect", params={"url": "Qwen/Qwen3-8B-GGUF", "agent": "pc"},
                          headers=headers).json()
    assert data["recommended"]["llamacpp"] == "Qwen3-8B-Q4_K_M.gguf"  # Q6_K + 8K context is over 8 GB
    q4 = next(f for f in data["files"] if f["path"] == "Qwen3-8B-Q4_K_M.gguf")
    assert q4["runtimes"]["llamacpp"]["best"] is True
    assert q4["optimized_for"]["runtime"] == "ollama"
    assert fit.importable_gguf("Qwen3-8B-Q4_K_M.gguf")


def test_language_models_are_not_checkpoints():
    from backend.app.hf_inspect import is_llm
    ggufs = gguf_files()
    assert is_llm("text-generation", [], "Qwen/Qwen3-8B-GGUF", ggufs)
    assert is_llm(None, ["gguf", "conversational"], "bartowski/Llama-3.1-8B-GGUF", ggufs)
    assert is_llm(None, ["gguf"], "someone/Mistral-7B-GGUF", ggufs)  # GGUF-only, nothing image-like
    assert not is_llm("text-to-image", ["gguf"], "city96/FLUX.1-dev-gguf", ggufs)
    assert not is_llm(None, ["gguf"], "city96/t5-v1_1-xxl-encoder-gguf", ggufs)
    assert not is_llm(None, ["gguf"], "QuantStack/Wan2.1-ComfyUI-GGUF", ggufs)
    sdxl = [{"path": "model.safetensors", "name": "model.safetensors"}]
    assert not is_llm(None, ["diffusers"], "user/sdxl-model", sdxl)


def test_inspect_llm_offers_no_comfyui_folder_and_recommends_for_llamacpp(tmp_path, monkeypatch):
    from backend.app import main
    monkeypatch.setattr(main, "DB", tmp_path / "db.sqlite3")
    monkeypatch.setattr(main, "TOKEN", "t")
    client, headers = TestClient(app), {"X-ModelHarbor-Token": "t"}
    client.post("/api/agents/heartbeat", headers=headers, json={
        "device": "pc", "comfyui_count": 0, "llamacpp_count": 0,
        "hardware": {"gpu": "RX 9070 XT", "vendor": "amd", "vram_bytes": 16 * GB, "ram_bytes": 64 * GB}})
    meta = {"id": "Qwen/Qwen3-8B-GGUF", "pipeline_tag": "text-generation", "tags": ["gguf", "qwen"], "cardData": {}}
    with patch("backend.app.hf_inspect.fetch", return_value=json.dumps(meta).encode()), \
            patch("backend.app.hf_inspect.list_model_files", return_value={"files": gguf_files()}):
        data = client.get("/api/huggingface/inspect", params={"url": "Qwen/Qwen3-8B-GGUF", "agent": "pc"},
                          headers=headers).json()
    assert data["model_type"] == "LLM"
    assert data["target_folder"] is None and data["available_folders"] == []
    assert data["base_model"] is None  # no "requires a Qwen base model" warning
    assert data["primary_file"]["path"] == data["recommended"]["llamacpp"] == "Qwen3-8B-Q8_0.gguf"
    assert "comfyui" not in data["recommended"]


def test_gguf_is_refused_into_checkpoints(tmp_path, monkeypatch):
    from backend.app import main
    monkeypatch.setattr(main, "DB", tmp_path / "db.sqlite3")
    monkeypatch.setattr(main, "TOKEN", "t")
    client, headers = TestClient(app), {"X-ModelHarbor-Token": "t"}
    with patch("backend.app.main.list_model_files", return_value={"files": gguf_files()}):
        res = client.post("/api/downloads/new", headers=headers, json={
            "agent": "pc", "provider": "huggingface", "repository": "Qwen/Qwen3-8B-GGUF",
            "remote_path": "Qwen3-8B-Q4_K_M.gguf", "folder": "checkpoints/Qwen"})
    assert res.status_code == 422 and "checkpoints" in res.json()["detail"]
