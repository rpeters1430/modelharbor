from backend.app.intelligence import enrich, family, physical_device

def record(name, path, size=10):
    return {"device":"windows-pc:comfyui","name":name,"path":path,
            "kind":"checkpoint","size_bytes":size,"modified_ns":0}

def test_possible_duplicate_different_paths():
    rows=enrich([record("PonyXL.safetensors","C:/a/PonyXL.safetensors"),
                 record("ponyxl.safetensors","D:/b/ponyxl.safetensors")])
    assert all(x["possible_duplicate"] for x in rows)
    assert all(x["duplicate_group_size"]==2 for x in rows)
    assert all(x["family_guess"]=="Pony" for x in rows)

def test_different_size_not_duplicate():
    rows=enrich([record("model.safetensors","a",10),record("model.safetensors","b",20)])
    assert not any(x["possible_duplicate"] for x in rows)

def test_device_grouping():
    assert physical_device("windows-pc:ollama")=="windows-pc"
    assert physical_device("windows-pc:comfyui")=="windows-pc"

def test_unknown_is_not_verified():
    item=enrich([record("unknown.safetensors","a")])[0]
    assert item["family_guess"]=="Unknown"
    assert item["family_verified"] is False

def test_hard_links_are_one_file():
    a=record("x.safetensors","C:/m/x.safetensors"); a["file_id"]="1:42"
    b=record("x.safetensors","C:/m/SDXL/x.safetensors"); b["file_id"]="1:42"
    rows=enrich([a,b])
    assert not any(x["possible_duplicate"] for x in rows)
    assert [x["hard_link"] for x in rows]==[False,True]
    c=record("x.safetensors","D:/x.safetensors"); c["file_id"]="2:7"
    assert all(x["possible_duplicate"] for x in enrich([a,b,c]))

def test_krea2_and_zimage_families():
    guess=lambda name,path:family(record(name,path))[0]
    assert guess("krea2_turbo_fp8.safetensors","C:/m/diffusion_models/krea2_turbo_fp8.safetensors")=="Krea 2"
    assert guess("muse.safetensors","C:/m/diffusion_models/Krea2/muse.safetensors")=="Krea 2"
    assert guess("flux1-krea-dev.safetensors","C:/m/diffusion_models/flux1-krea-dev.safetensors")=="FLUX"
    assert guess("z_image_turbo_bf16.safetensors","C:/m/diffusion_models/z_image_turbo_bf16.safetensors")=="Z-Image"
    assert family({**record("a.safetensors","a"),"source":{"base_model":"Krea 2 Turbo"}})==("Krea 2",True)


def test_lemonade_aliases_count_once_and_share_a_link():
    source = {"provider": "huggingface", "model_name": "o/r", "url": "https://huggingface.co/o/r"}
    rows = enrich([
        {"device": "pc:lemonade", "path": "lemonade://gemma4-12b", "name": "gemma4-12b", "kind": "lemonade/llamacpp",
         "size_bytes": 7, "file_id": "ckpt:abc", "source": source, "update": None},
        {"device": "pc:lemonade", "path": "lemonade://gemma4:12b", "name": "gemma4:12b", "kind": "lemonade/llamacpp",
         "size_bytes": 7, "file_id": "ckpt:abc", "source": None, "update": None},
    ])
    assert [r["hard_link"] for r in rows] == [False, True]
    assert rows[1]["source"] == source

def test_duplicates_are_decided_by_sha256_when_known():
    def model(path, sha):
        return {"device": "pc:comfyui", "path": path, "name": "x.safetensors", "kind": "lora", "size_bytes": 10, "sha256": sha}
    same = enrich([model("C:/a/x.safetensors", "a" * 64), model("C:/b/x.safetensors", "a" * 64)])
    assert all(r["possible_duplicate"] and r["duplicate_verified"] for r in same)
    different = enrich([model("C:/a/x.safetensors", "a" * 64), model("C:/b/x.safetensors", "b" * 64)])
    assert not any(r["possible_duplicate"] for r in different)
    unhashed = enrich([model("C:/a/x.safetensors", None), model("C:/b/x.safetensors", None)])
    assert all(r["possible_duplicate"] and not r["duplicate_verified"] for r in unhashed)
