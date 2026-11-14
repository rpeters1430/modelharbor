import json
import pytest
from fastapi import HTTPException
from backend.app.browse import huggingface_base_family, search_huggingface

def test_family_from_tags_and_name():
    assert huggingface_base_family({"id":"creator/neutral","tags":["base_model:stabilityai/stable-diffusion-xl-base-1.0"]})=="sdxl"
    assert huggingface_base_family({"id":"creator/FLUX.1-dev-LoRA","tags":["lora"]})=="flux"
    assert huggingface_base_family({"id":"creator/unknown","tags":["lora"]})==""

def test_pony_and_illustrious_not_mislabeled_as_sdxl():
    assert huggingface_base_family({"id":"owner/Pony-SDXL-LoRA","tags":["pony","sdxl"]})=="pony"
    assert huggingface_base_family({"id":"owner/Illustrious-SDXL","tags":["illustrious","sdxl"]})=="illustrious"

def test_filter_excludes_unknown_and_other_families():
    models=[{"id":"owner/FLUX.1-lora","tags":["lora","flux"]},
            {"id":"owner/SDXL-lora","tags":["lora","sdxl"]},
            {"id":"owner/untagged-lora","tags":["lora"]}]
    seen=[]
    def get(url):
        seen.append(url)
        return json.dumps(models)
    data=search_huggingface("","lora","Trending",get=get,base="flux")
    assert [x["id"] for x in data["items"]]==["owner/FLUX.1-lora"]
    assert data["items"][0]["base_label"]=="FLUX"
    assert "limit=100" in seen[0]

def test_invalid_family_rejected():
    with pytest.raises(HTTPException):
        search_huggingface("","lora","Trending",base="untrusted")
