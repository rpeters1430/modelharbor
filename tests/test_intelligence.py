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
