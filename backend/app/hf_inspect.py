"""Inspect Hugging Face model repositories and file links to identify base model,
model type, target ComfyUI folder, trigger words, and candidate files for download.
"""
import json
import re
from urllib.parse import quote, urlsplit
from fastapi import HTTPException
from .hf_repository import list_model_files, validate_repo
from .sources import fetch
from .browse import folder_for, valid_folder, FAMILY_FOLDERS, FOLDERS

FAMILY_PATTERNS = [
    ("MOSS", r"\bmoss\b", "MOSS"),
    ("PonyV7", r"pony[-_\s]*v7", "Pony V7"),
    ("Pony", r"\bpony\b", "Pony"),
    ("SDXL", r"\billustrious\b", "Illustrious"),
    ("SDXL", r"\bnoobai\b", "NoobAI"),
    ("SDXL", r"\bsdxl\b|stable[- ]?diffusion[- ]?xl", "SDXL 1.0"),
    ("SD15", r"\bsd[- ]?1\.?5\b|stable[- ]?diffusion[- ]?1\.?5\b|\bsd15\b|\bv1[- ]5\b", "SD 1.5"),
    ("SD2", r"\bsd[- ]?2(\.0|\.1)?\b|stable[- ]?diffusion[- ]?2", "SD 2.0"),
    ("SD3", r"\bsd[- ]?3(\.5)?\b|stable[- ]?diffusion[- ]?3", "SD 3.5"),
    ("Flux", r"\bflux(\.1)?\b", "Flux.1"),
    ("Qwen", r"\bqwen\b", "Qwen"),
    ("Wan", r"\bwan(\s*video)?[- ]?2?\.?1?\b", "Wan Video"),
    ("LTX", r"\bltx(\s*video)?\b", "LTX Video"),
    ("Hunyuan", r"\bhunyuan(\s*video)?\b", "Hunyuan"),
    ("Krea2", r"\bkrea[- ]?2\b", "Krea 2"),
    ("ZImage", r"\bz[- ]?image\b", "Z-Image"),
]

def parse_hf_url(url_or_repo: str) -> tuple[str, str | None]:
    """Parse a Hugging Face URL or owner/repo string into (repo, remote_path)."""
    s = (url_or_repo or "").strip()
    if not s:
        raise HTTPException(422, "Please enter a Hugging Face link or repository")
    if s.startswith(("http://", "https://")):
        parsed = urlsplit(s)
        if parsed.hostname not in ("huggingface.co", "www.huggingface.co", "hf.co"):
            raise HTTPException(422, "Not a Hugging Face URL")
        path = parsed.path.strip("/")
    else:
        path = s.strip("/")
        for prefix in ("https://huggingface.co/", "http://huggingface.co/", "huggingface.co/", "www.huggingface.co/", "hf.co/"):
            if path.startswith(prefix):
                path = path[len(prefix):]
                break

    parts = path.split("/")
    if len(parts) < 2:
        raise HTTPException(422, "Expected a Hugging Face repository in owner/repo format")
    repo = validate_repo(f"{parts[0]}/{parts[1]}")
    remote_path = None
    if len(parts) >= 4 and parts[2] in ("blob", "resolve") and len(parts) >= 5:
        remote_path = "/".join(parts[4:])
    return repo, remote_path

def detect_base_model(card_data: dict, tags: list[str], repo: str, files: list[dict]) -> tuple[str | None, str | None]:
    """Return (family, base_model_name) or (None, None)."""
    candidates = []
    bm = card_data.get("base_model")
    if isinstance(bm, list):
        candidates.extend(str(x) for x in bm if x)
    elif isinstance(bm, str) and bm:
        candidates.append(bm)

    for tag in tags:
        t = str(tag or "")
        if t.startswith("base_model:"):
            candidates.append(t.split(":", 1)[1])
        elif any(fam.lower() in t.lower() for fam, _, _ in FAMILY_PATTERNS):
            candidates.append(t)

    candidates.append(repo)
    for f in files[:10]:
        candidates.append(f.get("name", ""))

    text = " ".join(candidates).lower()
    for fam, pattern, label in FAMILY_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return fam, label
    return None, None

def detect_model_kind(card_data: dict, tags: list[str], repo: str, files: list[dict], remote_path: str | None = None) -> tuple[str, str]:
    """Return (model_type, folder_parent)."""
    tag_set = {str(t).lower() for t in tags}
    haystack = (repo + " " + " ".join(tags) + " " + (remote_path or "")).lower()

    if (
        "lora" in tag_set
        or "template:sd-lora" in tag_set
        or "lora" in (card_data.get("tags") or [])
        or card_data.get("base_model_relation") == "adapter"
        or "lora" in haystack
        or any("lora" in f.get("name", "").lower() for f in files)
    ):
        return "LORA", "loras"
    if "vae" in tag_set or "vae" in haystack or any("vae" in f.get("name", "").lower() for f in files):
        return "VAE", "vae"
    if "controlnet" in tag_set or "controlnet" in haystack:
        return "Controlnet", "controlnet"
    if "text-encoder" in tag_set or "text_encoder" in tag_set or "clip" in haystack:
        return "Text Encoder", "text_encoders"
    if "upscale" in haystack or "esrgan" in haystack:
        return "Upscaler", "upscale_models"
    if "embedding" in tag_set or "textual-inversion" in tag_set:
        return "TextualInversion", "embeddings"
    if "unet" in haystack or "diffusion_models" in haystack or "transformer" in haystack:
        return "Diffusion Model", "diffusion_models"
    return "Checkpoint", "checkpoints"

def score_file(file_info: dict, is_lora: bool, is_comfy: bool) -> int:
    """Heuristic scoring to choose the best candidate model file."""
    score = 0
    name = (file_info.get("name") or "").lower()
    path = (file_info.get("path") or "").lower()

    # Preferred format
    if name.endswith(".safetensors"):
        score += 50
    elif name.endswith((".gguf", ".ckpt", ".pt")):
        score += 15

    # Having a verified LFS SHA is crucial
    if file_info.get("sha256"):
        score += 40
    else:
        score -= 80

    # Root files are usually the primary artifact
    if "/" not in file_info.get("path", ""):
        score += 100

    # Explicit ComfyUI conversions/builds are top priority
    if "comfyui" in name or "comfy" in name:
        score += 200

    if is_lora:
        if "lora" in name:
            score += 50
        if "pytorch_lora_weights" in name:
            score += 25
        size = file_info.get("size_bytes") or 0
        if 5_000_000 <= size <= 1_500_000_000:
            score += 30

    # Penalize subfolders with intermediate checkpoints or merged models
    if "checkpoint-" in path or "checkpoint_" in path:
        score -= 60
    if "merged-model" in path or "original-checkpoint" in path:
        score -= 40
    if "test" in path or "sample" in path:
        score -= 40

    return score

def inspect_huggingface(url_or_repo: str, get=fetch) -> dict:
    """Inspect a Hugging Face repository or file link."""
    repo, remote_path = parse_hf_url(url_or_repo)
    body = get(f"https://huggingface.co/api/models/{quote(repo, safe='/')}")
    if not body:
        raise HTTPException(404, f"Hugging Face repository '{repo}' not found or inaccessible")
    data = json.loads(body)

    card = data.get("cardData") or {}
    tags = data.get("tags") or []
    listing = list_model_files(repo)
    files = listing.get("files") or []

    family, base_label = detect_base_model(card, tags, repo, files)
    model_type, folder_parent = detect_model_kind(card, tags, repo, files, remote_path)
    target_folder = folder_for(model_type, None, family or base_label)

    # Trigger words / instance prompt
    trigger_words = []
    if card.get("instance_prompt"):
        prompt = str(card["instance_prompt"]).strip()
        if prompt:
            trigger_words.append(prompt)
    if isinstance(card.get("trained_words"), list):
        for w in card["trained_words"]:
            if w and str(w).strip() and str(w).strip() not in trigger_words:
                trigger_words.append(str(w).strip())

    # Candidate file selection
    primary_file = None
    if remote_path:
        primary_file = next((f for f in files if f["path"] == remote_path), None)

    is_lora = model_type == "LORA"
    is_comfy = "comfy" in repo.lower()
    scored_files = []
    for f in files:
        sc = score_file(f, is_lora, is_comfy)
        scored_files.append((sc, f))

    scored_files.sort(key=lambda x: (x[0], x[1].get("size_bytes") or 0), reverse=True)
    if not primary_file and scored_files:
        primary_file = scored_files[0][1]

    # Available target folders
    available_folders = [target_folder]
    for p in ("loras", "checkpoints", "diffusion_models", "text_encoders", "vae", "embeddings", "controlnet", "upscale_models"):
        if p not in available_folders:
            available_folders.append(p)
    if family:
        for p in ("loras", "checkpoints", "diffusion_models", "controlnet"):
            sub = f"{p}/{family}"
            if sub not in available_folders:
                available_folders.append(sub)

    return {
        "provider": "huggingface",
        "repository": repo,
        "url": f"https://huggingface.co/{repo}",
        "specified_path": remote_path,
        "model_type": model_type,
        "base_model": base_label,
        "family": family,
        "target_folder": target_folder,
        "available_folders": available_folders,
        "trigger_words": trigger_words,
        "primary_file": primary_file,
        "files": files,
        "downloads": data.get("downloads"),
        "likes": data.get("likes"),
        "created_at": data.get("createdAt"),
    }
