"""Read-only dependency audit for ComfyUI workflow JSON exports.

Supports UI workflow nodes and API prompt graphs. Never executes workflow data.
"""
from pathlib import PurePath
from fastapi import HTTPException

MODEL_INPUTS = {
    "ckpt_name": "checkpoint", "unet_name": "diffusion", "lora_name": "lora",
    "vae_name": "vae", "clip_name": "text_encoder", "clip_name1": "text_encoder",
    "clip_name2": "text_encoder", "clip_name3": "text_encoder",
    "control_net_name": "controlnet", "upscale_model": "upscaler",
    "model_name": "model", "text_encoder_name": "text_encoder",
}
EXTENSIONS = (".safetensors", ".sft", ".ckpt", ".pt", ".pth", ".gguf", ".bin", ".onnx")

def _safe_name(value):
    if not isinstance(value, str) or len(value) > 512:
        return None
    value = value.strip().replace("\\", "/")
    if not value or value.startswith("/") or ".." in value.split("/"):
        return None
    if not value.lower().endswith(EXTENSIONS):
        return None
    return value

def _dependencies(data):
    if not isinstance(data, dict):
        raise HTTPException(422, "Workflow must be a JSON object")
    nodes = data.get("nodes")
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            title = str(node.get("type") or "Node")[:100]
            widgets = node.get("widgets_values")
            if isinstance(widgets, list):
                for value in widgets:
                    name = _safe_name(value)
                    if name:
                        yield name, "model", title
            elif isinstance(widgets, dict):
                for key, value in widgets.items():
                    if key in MODEL_INPUTS:
                        name = _safe_name(value)
                        if name:
                            yield name, MODEL_INPUTS[key], title
    graph = data.get("prompt", data)
    if isinstance(graph, dict):
        for node in graph.values():
            if not isinstance(node, dict):
                continue
            title = str(node.get("class_type") or "Node")[:100]
            inputs = node.get("inputs") or {}
            if not isinstance(inputs, dict):
                continue
            for key, value in inputs.items():
                if key in MODEL_INPUTS:
                    name = _safe_name(value)
                    if name:
                        yield name, MODEL_INPUTS[key], title

def audit(data, inventory, device=""):
    dependencies = {}
    for name, kind, node in _dependencies(data):
        key = name.casefold()
        if key not in dependencies:
            dependencies[key] = {"name": name, "kind": kind, "nodes": [node]}
        elif node not in dependencies[key]["nodes"]:
            dependencies[key]["nodes"].append(node)
    results = []
    for dep in dependencies.values():
        matches = [m for m in inventory if str(m.get("device", "")).endswith(":comfyui")
                   and (not device or m["device"] == device)
                   and str(m.get("name") or "").casefold() == PurePath(dep["name"]).name.casefold()]
        exact = [m for m in matches if str(m.get("path") or "").replace("\\", "/").casefold().endswith(dep["name"].casefold())]
        chosen = exact or matches
        results.append({**dep, "status": "installed" if exact else "possible_match" if chosen else "missing",
                        "matches": [{"device": m["device"], "path": m["path"]} for m in chosen[:5]]})
    return {"dependencies": results, "total": len(results),
            "missing": sum(x["status"] == "missing" for x in results),
            "note": "UI workflow exports may omit hidden or custom-node dependencies. A filename match is not proof of model compatibility."}
