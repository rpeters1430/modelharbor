"""Offline tests: no network calls or model downloads."""
from unittest.mock import patch
import pytest
from fastapi import HTTPException
from backend.app.hf_repository import (
    list_model_files, validate_repo, _next_cursor, _cache,
)

def test_repo_validation_blocks_urls_and_traversal():
    for name in ("https://huggingface.co/x/y", "../repo", "x/../y", "owner/repo/other", "owner\\repo"):
        with pytest.raises(HTTPException):
            validate_repo(name)
    assert validate_repo("pmczip/MOSS_Image_LoRAs") == "pmczip/MOSS_Image_LoRAs"

def test_collection_lists_multiple_individual_loras():
    _cache.clear()
    page = [
        {"type":"file","path":"Alpha_01.safetensors","size":145000000,
         "lfs":{"oid":"a"*64,"size":145000000,"pointerSize":132}},  # real Hub shape
        {"type":"file","path":"sub/Beta_02.safetensors","size":145000000},
        {"type":"file","path":"README.md","size":123},
        {"type":"directory","path":"sub"},
    ]
    with patch("backend.app.hf_repository._json_page", return_value=(page,None)) as fetch:
        result = list_model_files("pmczip/MOSS_Image_LoRAs")
    assert result["complete"] is True
    assert len(result["files"]) == 2
    assert result["folders"] == ["sub"]
    assert result["files"][0]["sha256"] == "a"*64
    assert result["files"][1]["sha256"] is None
    assert "/blob/main/sub/Beta_02.safetensors" in result["files"][1]["url"]
    assert fetch.call_count == 1

def test_filename_filter_and_cache():
    _cache.clear()
    page = [
        {"type":"file","path":"LoRAs/Anna_Model.safetensors","size":120},
        {"type":"file","path":"LoRAs/Bella_Model.safetensors","size":125},
    ]
    with patch("backend.app.hf_repository._json_page",return_value=(page,None)) as fetch:
        result = list_model_files("owner/multi","anna")
        again = list_model_files("owner/multi","anna")
    assert result == again
    assert len(result["files"]) == 1
    assert fetch.call_count == 1

def test_next_cursor_only_from_official_host():
    assert _next_cursor('<https://evil.example/api?cursor=x>; rel="next"') is None
    assert _next_cursor('<https://huggingface.co/api/models/a/b/tree/main?cursor=abc>; rel="next"') == "abc"

def test_partial_listing_is_explicit():
    _cache.clear()
    with patch("backend.app.hf_repository.MAX_PAGES",1):
        with patch("backend.app.hf_repository._json_page",return_value=(
            [{"type":"file","path":"one.safetensors","size":1}],"still-more"
        )):
            result=list_model_files("owner/huge")
    assert result["complete"] is False
    assert result["note"]
