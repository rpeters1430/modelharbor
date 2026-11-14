"""Read-only ComfyUI model folder scanner (Windows and Linux)."""
import argparse
import os
from pathlib import Path

EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx"}
KINDS = {"checkpoints":"checkpoint", "loras":"lora", "vae":"vae",
         "embeddings":"embedding", "controlnet":"controlnet",
         "diffusion_models":"diffusion", "unet":"diffusion",
         "text_encoders":"text_encoder", "clip":"text_encoder",
         "upscale_models":"upscaler"}

def scan(root: Path, device: str):
    found = []
    if not root.is_dir():
        raise ValueError(f"Model folder does not exist: {root}")
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        kind = KINDS.get(directory.name.lower(), directory.name.lower())
        for base, dirs, files in os.walk(directory, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
            for filename in files:
                path = Path(base) / filename
                if path.suffix.lower() not in EXTENSIONS or path.is_symlink():
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                found.append({"device":device, "path":str(path), "name":filename,
                    "kind":kind, "size_bytes":stat.st_size, "modified_ns":stat.st_mtime_ns,
                    # Hard links share volume + file index; lets the server count them once.
                    "file_id":f"{stat.st_dev}:{stat.st_ino}" if stat.st_ino else None})
    return found

def main():
    # Preview only: modelharbor_agent.py uploads, under the device name in its config.
    p = argparse.ArgumentParser(description="Preview the ComfyUI models the agent would find")
    p.add_argument("--models-dir", type=Path, required=True)
    p.add_argument("--dry-run", action="store_true", help="Accepted for older instructions; this script never uploads")
    args = p.parse_args()
    models = scan(args.models_dir, "preview:comfyui")
    print(f"Found {len(models)} models")
    for model in models[:20]:
        print(model["kind"], model["name"])

if __name__ == "__main__":
    main()
