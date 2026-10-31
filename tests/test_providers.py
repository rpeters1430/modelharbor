from unittest.mock import patch
import pytest
from fastapi import HTTPException
from backend.app.providers import search, CACHE

def test_invalid_provider_rejected():
    with pytest.raises(HTTPException):
        search("other", "some model")

def test_short_query_rejected():
    with pytest.raises(HTTPException):
        search("huggingface", "a")

def test_hf_results_not_marked_verified():
    CACHE.clear()
    with patch("backend.app.providers.get_json", return_value=[{"id":"author/model","downloads":5}]) as mock:
        rows=search("huggingface","unique-search-phrase")
    assert rows[0]["url"]=="https://huggingface.co/author/model"
    assert "verified" not in rows[0]
    assert mock.call_count==1
