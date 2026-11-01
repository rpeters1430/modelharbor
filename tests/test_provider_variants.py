from unittest.mock import patch
from backend.app.providers import CACHE, variants, search


def test_filename_cleaning_and_version_removal():
    names = variants("bigLust_v16.safetensors")
    assert "bigLust v16" in names
    assert "bigLust" in names


def test_huggingface_falls_back_when_full_name_has_no_results():
    CACHE.clear()
    with patch("backend.app.providers.get_json", side_effect=[[], [{"id": "author/bigLust"}]]) as fetch:
        results = search("huggingface", "bigLust_v16.safetensors")
    assert results[0]["id"] == "author/bigLust"
    assert fetch.call_count == 2


def test_first_result_stops_search():
    CACHE.clear()
    with patch("backend.app.providers.get_json", return_value=[{"id": "owner/model"}]) as fetch:
        search("huggingface", "unique-very-long-name-abc")
    assert fetch.call_count == 1
