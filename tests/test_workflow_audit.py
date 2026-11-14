from backend.app.workflow_audit import audit

def test_api_workflow_reports_missing_and_installed():
    workflow={"1":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":"models\\\\base.safetensors"}},
              "2":{"class_type":"LoraLoader","inputs":{"lora_name":"style.safetensors"}}}
    inventory=[{"device":"PC:comfyui","path":"checkpoints/base.safetensors","name":"base.safetensors"}]
    result=audit(workflow,inventory)
    assert result["total"]==2
    assert result["missing"]==1
    assert result["dependencies"][0]["status"]=="possible_match"
    assert result["dependencies"][1]["status"]=="missing"

def test_ui_workflow_and_deduplication():
    workflow={"nodes":[{"type":"CheckpointLoaderSimple","widgets_values":["base.safetensors"]},
                       {"type":"CheckpointLoaderSimple","widgets_values":["base.safetensors"]}]}
    result=audit(workflow,[{"device":"PC:comfyui","path":"models/checkpoints/base.safetensors","name":"base.safetensors"}])
    assert result["total"]==1
    assert result["missing"]==0

def test_reject_path_traversal():
    result=audit({"1":{"inputs":{"ckpt_name":"../../secret.safetensors"}}},[])
    assert result["total"]==0
