"""Shared test setup. Tests never reach the network."""
import pytest


@pytest.fixture(autouse=True)
def no_gguf_header_downloads(monkeypatch):
    """Reading a GGUF's header is a ranged download from Hugging Face; tests that need one stub it."""
    from backend.app import gguf_meta

    def offline(url, size=None):
        raise OSError("network disabled in tests: " + url)
    monkeypatch.setattr(gguf_meta, "fetch_range", offline)
    gguf_meta._cache.clear()
