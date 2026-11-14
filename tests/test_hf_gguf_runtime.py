"""Regression checks for Hugging Face GGUF runtime recommendations."""
from backend.app.fit import GB, annotate_listing

def test_gguf_has_llm_specific_recommendation():
    hw={"gpu":"AMD Radeon RX 9070 XT","vendor":"amd","vram_bytes":16*GB,"ram_bytes":128*GB}
    listing={"files":[{"name":"coder-Q4_K_M.gguf","path":"coder-Q4_K_M.gguf","size_bytes":9*GB}]}
    result=annotate_listing(listing,hw,hw)
    file=result["files"][0]
    assert file["llm_fit"]["level"]=="good"
    assert file["llm_recommendation"]["score"] is not None
    assert "8K context" in file["llm_fit"]["note"]

def test_gguf_unknown_runtime_does_not_claim_llm_fit():
    hw={"gpu":"AMD Radeon RX 9070 XT","vendor":"amd","vram_bytes":16*GB,"ram_bytes":128*GB}
    listing={"files":[{"name":"coder-Q4_K_M.gguf","path":"coder-Q4_K_M.gguf","size_bytes":9*GB}]}
    result=annotate_listing(listing,hw)
    assert "llm_fit" not in result["files"][0]
    assert "llm_recommendation" not in result["files"][0]
