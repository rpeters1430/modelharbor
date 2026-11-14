"""Recommendation heuristics regression tests."""
from backend.app.fit import GB, recommend_file, mark_recommended

AMD = {"gpu": "Radeon RX 9070 XT", "vendor": "amd", "vram_bytes": 16 * GB, "ram_bytes": 128 * GB}

def test_nvidia_only_not_recommended_on_amd():
    result = recommend_file("model_nvfp4.safetensors", 5 * GB, AMD)
    assert result["score"] == 0
    assert result["label"] == "Incompatible"

def test_safe_format_beats_pickle():
    safe = recommend_file("model_fp16.safetensors", 5 * GB, AMD)
    unsafe = recommend_file("model_fp16.ckpt", 5 * GB, AMD)
    assert safe["score"] > unsafe["score"]

def test_unknown_hardware_not_fabricated():
    assert recommend_file("model.safetensors", 4 * GB, None)["score"] is None

def test_smaller_equivalent_variant_wins_on_score_tie():
    files = [
        {"name": "model_fp16.safetensors", "size_bytes": 10 * GB, "recommendation": {"score": 88}},
        {"name": "model_fp8.safetensors", "size_bytes": 6 * GB, "recommendation": {"score": 88}},
    ]
    mark_recommended(files, lambda f: "model")
    assert files[1]["best"] is True
    assert not files[0].get("best")
