import json
from backend.app.browse import search_huggingface

def test_huggingface_discovery_metadata():
    payload=[{"id":"example/Model-GGUF","downloads":1234,"likes":42,
              "pipeline_tag":"text-to-image","tags":["gguf","safetensors"],
              "gated":"auto","lastModified":"2026-11-01T00:00:00Z"}]
    result=search_huggingface("model","","Trending",get=lambda url: json.dumps(payload))
    item=result["items"][0]
    assert item["pipeline_tag"]=="text-to-image"
    assert item["gated"] is True
    assert "gguf" in item["tags"]
    assert item["updated_at"]=="2026-11-01T00:00:00Z"

def test_huggingface_metadata_missing_is_safe():
    result=search_huggingface("","","Trending",get=lambda url: json.dumps([{"id":"owner/repo"}]))
    item=result["items"][0]
    assert item["tags"]==[]
    assert item["pipeline_tag"]==""
    assert item["gated"] is False
