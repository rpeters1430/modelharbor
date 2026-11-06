"""Rate downloadable files for a PC's GPU and memory, and pick the best of each set of variants.

Advice only, from the file's name, size and Civitai's format metadata. Sizes are compared with the weights alone;
ComfyUI also needs room for text encoders, the VAE and activations, hence the headroom."""
import re

GB = 1024**3
HEADROOM = 2 * GB
# Formats that only run on NVIDIA GPUs (Blackwell FP4, Nunchaku SVDQuant).
NVIDIA_ONLY = re.compile(r"nvfp4|mxfp4|svdq|nunchaku|(?<![a-z0-9])fp4(?![a-z0-9])", re.I)
PICKLE = (".ckpt", ".pt", ".pth", ".bin")
MODEL_FILES = (".safetensors", ".sft", ".gguf", ".onnx") + PICKLE
# Words that name a precision or packaging of the same model, so its variants group together.
VARIANT = re.compile(r"(?<![a-z0-9])(fp8|fp16|fp32|bf16|f16|f32|fp4|nvfp4|mxfp4|e4m3fn|e5m2|scaled|int8|int4|nf4|svdq|"
                     # q8_0 and q4_0/q5_1 before the general quant, which would otherwise stop at "q8" and
                     # leave "_0" behind, so those files never grouped with their sibling quants.
                     r"mxfp8|fp8mixed|mixed|convrot|w4a8|w8a8|w4a16|pruned|full|emaonly|q8_0|q[45]_[01]|"
                     r"iq[1-4]_(?:xxs|xs|s|m|nl)|q[2-8](?:_k)?(?:_[sml])?)(?![a-z0-9])", re.I)


# Names that already say 8-bit or smaller: a user's own conversion, or nothing left to convert.
COMPACT = re.compile(r"(?<![a-z0-9])(fp8\w*|e4m3fn|e5m2|scaled|int[48]\w*|convrot|nf4|fp4|nvfp4|mxfp[48]|w4a\d+|w8a8|"
                     r"q8_0|q[45]_[01]|q[2-8](?:_k)?(?:_[sml])?)(?![a-z0-9])", re.I)


def name_stem(name):
    """File name without extension and precision words: "Model_v40_int8_convrot.safetensors" -> "model_v40"."""
    return re.sub(r"[\s._-]+", "_", VARIANT.sub("", name.rsplit(".", 1)[0].lower())).strip("_")


def conversion_of(local, remote):
    """Whether the local file looks like the user's own 8-bit conversion of the remote one. Civitai glues the
    version name onto file names ("x3" -> "x3BF16INT8NVFP4"), so the local name only has to start the remote's."""
    stem = name_stem(local)
    return bool(COMPACT.search(local)) and len(stem) >= 8 and name_stem(remote).startswith(stem)


def _gb(n):
    return f"{n / GB:.0f} GB"


def rate(name, size, hw, meta=None):
    """{"level": "good" | "ok" | "bad", "note": ...} for one file, or None without hardware info."""
    if not hw or not size:
        return None
    meta, lower = meta or {}, name.lower()
    gpu, vram, ram = hw.get("gpu") or "GPU", hw.get("vram_bytes") or 0, hw.get("ram_bytes") or 0
    if not lower.endswith(MODEL_FILES):
        return {"level": "bad", "note": "Not a file ComfyUI loads (a Diffusers zip, for example)"}
    if hw.get("vendor") != "nvidia" and NVIDIA_ONLY.search(name):
        return {"level": "bad", "note": f"NVIDIA-only format; won't run on your {gpu}"}
    if vram and size <= vram - HEADROOM:
        level, note = "good", f"Fits in your {_gb(vram)} of VRAM"
    elif ram and size <= ram // 2:
        level, note = "ok", f"Bigger than your {_gb(vram)} of VRAM: runs by offloading to RAM, slower"
    else:
        return {"level": "bad", "note": "Too big for this PC"}
    if lower.endswith(PICKLE) or meta.get("format") == "PickleTensor":
        level, note = "ok", note + "; older pickle format, prefer a .safetensors file"
    elif meta.get("fp") == "fp32" or re.search(r"(?<![a-z0-9])(fp32|f32)(?![a-z0-9])", lower):
        level, note = "ok", note + "; fp32 is twice the size of fp16 with no visible gain"
    return {"level": level, "note": note}


def variant_key(path):
    """Directory plus file name with precision words removed: fp16, fp8 and Q4_K_M copies share it."""
    folder, _, name = path.replace("\\", "/").rpartition("/")
    stem = VARIANT.sub("", name.rsplit(".", 1)[0].lower())
    return folder.lower() + "/" + re.sub(r"[\s._-]+", "_", stem).strip("_")


# An LLM needs room beyond its weights for the context (KV) cache and compute buffers.
# About 10% plus 1.5 GB covers an 8K context for most models; longer contexts need more.
LLM_OVERHEAD = 1.5 * GB
LLM_OVERHEAD_RATIO = 1.1

def rate_llm(name, size, hw):
    """Like rate(), for a GGUF the Lemonade PC would run: its GPU, RAM and free disk, with context headroom."""
    if not hw or not size or not name.lower().endswith(".gguf"):
        return None
    gpu, vram, ram, free = hw.get("gpu") or "GPU", hw.get("vram_bytes") or 0, hw.get("ram_bytes") or 0, hw.get("free_bytes")
    if free is not None and size + HEADROOM > free:
        return {"level": "bad", "note": f"Not enough free disk on the Lemonade PC ({_gb(free)} free)"}
    needed = size * LLM_OVERHEAD_RATIO + LLM_OVERHEAD
    if vram and needed <= vram:
        return {"level": "good", "note": f"Fits in the {gpu}'s {_gb(vram)} of VRAM with room for an 8K context"}
    if ram and needed <= vram + ram * 0.75:
        return {"level": "ok", "note": f"Bigger than the {_gb(vram)} of VRAM: part runs from system RAM, slower"}
    return {"level": "bad", "note": "Too big for the Lemonade PC"}

def mark_best(files, key, fit_field="fit", best_field="best"):
    """Set "best" on the file to download from each group of two or more variants: the largest (highest
    precision) that fits in VRAM, else the smallest that runs at all."""
    groups = {}
    for f in files:
        groups.setdefault(key(f), []).append(f)
    for group in groups.values():
        rated = [f for f in group if f.get(fit_field) and f[fit_field]["level"] != "bad"]
        if len(group) < 2 or not rated:
            continue
        good = [f for f in rated if f[fit_field]["level"] == "good"]
        pick = max(good, key=lambda f: f["size_bytes"]) if good else min(rated, key=lambda f: f["size_bytes"])
        pick[best_field] = True


def annotate_listing(listing, hw, llm_hw=None):
    """Rate a Hugging Face or ModelScope file listing in place: "fit" for the agent's PC (ComfyUI) and,
    for GGUF files, "llm_fit" for the Lemonade PC."""
    if hw:
        for f in listing["files"]:
            f["fit"] = rate(f["name"], f.get("size_bytes"), hw)
        mark_best(listing["files"], lambda f: variant_key(f["path"]))
    if llm_hw:
        for f in listing["files"]:
            f["llm_fit"] = rate_llm(f["name"], f.get("size_bytes"), llm_hw)
        mark_best(listing["files"], lambda f: variant_key(f["path"]), "llm_fit", "llm_best")
    return listing
