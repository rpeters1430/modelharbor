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
# Without the model's shape, about 10% plus 1.5 GB covers an 8K context for most models.
LLM_OVERHEAD = 1.5 * GB
LLM_OVERHEAD_RATIO = 1.1
DEFAULT_CONTEXT = 8192
# With the shape known (gguf_meta.kv_bytes_per_token), the KV cache is counted exactly and this covers
# llama.cpp's compute buffers and the runtime itself.
COMPUTE_OVERHEAD = 0.75 * GB


def _ctx(tokens):
    return f"{tokens // 1024}K" if tokens % 1024 == 0 else f"{tokens:,}-token"


def _a(word):
    """ "an 8K", "an 11K", "an 18K" but "a 16K": the article for how a number is said."""
    return ("an " if word.startswith("8") or word.startswith(("11", "18")) and not word.startswith(("110", "180")) else "a ") + word


def llm_memory(size, context=DEFAULT_CONTEXT, kv_per_token=None):
    """Bytes of GPU (or GPU + RAM) memory a GGUF needs for its weights plus a context of that many tokens."""
    if kv_per_token:
        return size + kv_per_token * context + COMPUTE_OVERHEAD + size * 0.02
    return size * LLM_OVERHEAD_RATIO + LLM_OVERHEAD * max(1.0, context / DEFAULT_CONTEXT)


def rate_llm(name, size, hw, runtime="Lemonade", context=None, kv_per_token=None):
    """Rate a GGUF for an LLM runtime PC using its GPU, RAM, and optional free disk with context headroom.
    kv_per_token (from the GGUF's header) makes the context's share exact instead of a rule of thumb."""
    if not hw or not size or not name.lower().endswith(".gguf"):
        return None
    context = context or DEFAULT_CONTEXT
    gpu, vram, ram, free = hw.get("gpu") or "GPU", hw.get("vram_bytes") or 0, hw.get("ram_bytes") or 0, hw.get("free_bytes")
    if free is not None and size + HEADROOM > free:
        return {"level": "bad", "note": f"Not enough free disk on the {runtime} PC ({_gb(free)} free)"}
    needed = llm_memory(size, context, kv_per_token)
    detail = f"; needs about {needed / GB:.1f} GB with a {_ctx(context)} context" if kv_per_token else ""
    if vram and needed <= vram:
        return {"level": "good", "note": f"Fits in the {gpu}'s {_gb(vram)} of VRAM with room for {_a(_ctx(context))} context"
                + (f" (about {needed / GB:.1f} GB)" if kv_per_token else ""), "needed_bytes": int(needed)}
    if ram and needed <= vram + ram * 0.75:
        return {"level": "ok", "note": f"Bigger than the {_gb(vram)} of VRAM{detail}: part runs from system RAM, slower",
                "needed_bytes": int(needed)}
    return {"level": "bad", "note": f"Too big for the {runtime} PC{detail}", "needed_bytes": int(needed)}

# Unquantized GGUFs: twice the size of Q8_0 for no quality anyone can see, so a quant that fits wins.
FULL_PRECISION_GGUF = re.compile(r"(?<![a-z0-9])(bf16|f16|fp16|f32|fp32)(?![a-z0-9])", re.I)


def _pick(rated, fit_field):
    """The file to download among variants that run: the largest (highest precision) that fits in VRAM,
    preferring a quantized GGUF over an unquantized one, else the smallest that runs at all."""
    rated = [f for f in rated if f.get(fit_field) and f[fit_field]["level"] != "bad"]
    if not rated:
        return None
    good = [f for f in rated if f[fit_field]["level"] == "good"]
    if good:
        return max(good, key=lambda f: (not (f["name"].lower().endswith(".gguf") and FULL_PRECISION_GGUF.search(f["name"])),
                                        f.get("size_bytes") or 0))
    return min(rated, key=lambda f: f.get("size_bytes") or 0)


def mark_best(files, key, fit_field="fit", best_field="best"):
    """Set "best" on the file to download from each group of two or more variants (see _pick)."""
    groups = {}
    for f in files:
        groups.setdefault(key(f), []).append(f)
    for group in groups.values():
        pick = _pick(group, fit_field) if len(group) >= 2 else None
        if pick:
            pick[best_field] = True



def recommend_file(name, size, hw, meta=None, runtime="comfyui", context=None, kv_per_token=None):
    """Explainable heuristic recommendation; never present estimates as benchmarks."""
    fit = rate_llm(name, size, hw, runtime, context, kv_per_token) if runtime != "comfyui" else rate(name, size, hw, meta)
    if fit is None:
        return {"score": None, "label": "Unknown", "reasons": ["Hardware or file size unavailable"], "confidence": "low"}
    if fit["level"] == "bad":
        return {"score": 0, "label": "Incompatible", "level": "bad", "reasons": [fit["note"]], "confidence": "medium"}
    score = 80 if fit["level"] == "good" else 45
    reasons = [fit["note"]]
    lower = name.lower()
    if lower.endswith(".safetensors"):
        score += 8
        reasons.append("Safer tensor format")
    if lower.endswith(PICKLE):
        score -= 15
        reasons.append("Legacy pickle format; prefer safetensors")
    if re.search(r"(?<![a-z0-9])(fp32|f32)(?![a-z0-9])", lower):
        score -= 12
        reasons.append("FP32 may use substantially more memory than FP16")
    if runtime != "comfyui" and re.search(r"q[2-3](?:_|\\b)|iq[1-3]_", lower):
        score -= 12
        reasons.append("Aggressive quantization may reduce quality")
    if runtime != "comfyui" and re.search(r"q[45]_k_[ms]", lower):
        score += 5
        reasons.append("Balanced GGUF quantization candidate")
    score = max(0, min(100, score))
    return {"score": score, "label": "Recommended" if score >= 80 else "Usable with tradeoffs" if score >= 40 else "Not recommended",
            "level": fit["level"], "reasons": reasons, "confidence": "medium", "estimated": True}


def mark_recommended(files, key, fit_field="fit", best_field="best"):
    """Mark the file to download from each group of variants, never a known-incompatible one. Among copies
    that fit in VRAM the best-scored wins (safetensors over pickle, fp16 over fp32), and of those the
    largest, which is the highest precision. When nothing fits, the smallest copy that runs: offloading
    to RAM costs more speed the more of the model is left over."""
    groups = {}
    for f in files:
        groups.setdefault(key(f), []).append(f)
    for group in groups.values():
        eligible = [f for f in group if (f.get("recommendation") or {}).get("score")]
        if len(group) < 2 or not eligible:
            continue
        good = [f for f in eligible if (f["recommendation"].get("level") or f.get(fit_field, {}).get("level")) == "good"]
        chosen = (max(good, key=lambda f: (f["recommendation"]["score"], f.get("size_bytes") or 0)) if good
                  else min(eligible, key=lambda f: f.get("size_bytes") or 0))
        chosen[best_field] = True


# Which LLM server a GGUF suits. llama.cpp and Ollama both run GGUFs, but not all of them equally well.
SPLIT_GGUF = re.compile(r"(?:^|[-_.])\d{5}-of-\d{5}$", re.I)
IQUANT = re.compile(r"(?<![a-z0-9])(iq[1-4]_(?:xxs|xs|s|m|nl)|tq[12]_0)(?![a-z0-9])", re.I)
UNSLOTH_DYNAMIC = re.compile(r"(?<![a-z0-9])ud[-_]", re.I)
STANDARD_QUANT = re.compile(r"(?<![a-z0-9])(q[2-6]_k(?:_[sml])?|q[458]_[01])(?![a-z0-9])", re.I)
MOE = re.compile(r"(?<![a-z0-9])(a\d+(?:\.\d+)?b|moe|mixtral|gpt-oss|\d+x\d+b)(?![a-z0-9])", re.I)
RUNTIME_LABELS = {"llamacpp": "llama.cpp", "ollama": "Ollama", "lemonade": "Lemonade", "lmstudio": "LM Studio", "unsloth": "Unsloth"}


def importable_gguf(name):
    """A single-file GGUF model an LLM server can load by itself: not one shard of a split model and not
    a vision projector."""
    stem = name.rsplit("/", 1)[-1]
    if not stem.lower().endswith(".gguf"):
        return False
    stem = stem[:-5]
    return "mmproj" not in stem.lower() and not SPLIT_GGUF.search(stem)


def gguf_runtime(name, size=None, hw=None, context=None, kv_per_token=None, moe=None):
    """{"runtime": "llamacpp" | "ollama" | "either", "reason": ..., "ollama_ok": bool} for a GGUF, or None.

    hw is the PC that runs llama.cpp and Ollama (the agent's PC): a mixture-of-experts model bigger than its
    VRAM runs much faster in llama.cpp, which can keep the experts in system RAM."""
    lower = (name or "").rsplit("/", 1)[-1].lower()
    if not lower.endswith(".gguf"):
        return None
    stem = lower[:-5]
    if "mmproj" in stem:
        return {"runtime": "llamacpp", "ollama_ok": False,
                "reason": "Vision projector, not a model: llama.cpp loads it next to its model with --mmproj"}
    if SPLIT_GGUF.search(stem):
        return {"runtime": "llamacpp", "ollama_ok": False,
                "reason": "One part of a split model: llama.cpp loads split GGUFs, Ollama can't import them"}
    vram = (hw or {}).get("vram_bytes") or 0
    if (moe if moe is not None else MOE.search(stem)) and vram and size and llm_memory(size, context or DEFAULT_CONTEXT, kv_per_token) > vram:
        return {"runtime": "llamacpp", "ollama_ok": True,
                "reason": f"Mixture-of-experts model bigger than your {_gb(vram)} of VRAM: llama.cpp can keep the "
                          "experts in system RAM (--cpu-moe), which runs much faster than Ollama's layer split"}
    if IQUANT.search(stem):
        return {"runtime": "llamacpp", "ollama_ok": True,
                "reason": "I-quant (IQ/TQ): llama.cpp gets the fastest kernels for these first; Ollama's own "
                          "library ships K-quants"}
    if UNSLOTH_DYNAMIC.search(stem):
        return {"runtime": "llamacpp", "ollama_ok": True,
                "reason": "Unsloth Dynamic quant: Unsloth tunes and tests these with llama.cpp"}
    if STANDARD_QUANT.search(stem):
        return {"runtime": "ollama", "ollama_ok": True,
                "reason": "Standard quant, the kind Ollama's own library uses: imports straight into Ollama "
                          "(and runs in llama.cpp too)"}
    return {"runtime": "either", "ollama_ok": True, "reason": "Runs the same in llama.cpp and Ollama"}


def recommended_gguf(files, runtime):
    """The GGUF to download for one LLM server: the best that fits that server's PC, else a Q4_K_M, the usual
    balance of size and quality, when its hardware is unknown. None without a single-file GGUF."""
    names = {}
    for f in files:
        names[f["name"].lower()] = names.get(f["name"].lower(), 0) + 1
    candidates = [f for f in files if importable_gguf(f["path"]) and names[f["name"].lower()] == 1]
    if not candidates:
        return None
    if runtime != "llamacpp":  # leave llama.cpp-tuned files (I-quants, Unsloth Dynamic) to llama.cpp when possible
        suited = [f for f in candidates if (f.get("optimized_for") or {}).get("runtime") != "llamacpp"]
        if suited and (pick := recommended_gguf_among(suited, runtime)):
            return pick
    return recommended_gguf_among(candidates, runtime)


def recommended_gguf_among(candidates, runtime):
    """recommended_gguf for an already filtered list of single-file GGUFs."""
    rated = [f for f in candidates if (f.get("runtimes") or {}).get(runtime, {}).get("fit")]
    if rated:
        for f in rated:
            f["_fit"] = f["runtimes"][runtime]["fit"]
        pick = _pick(rated, "_fit")
        for f in rated:
            f.pop("_fit", None)
        return pick
    return next((f for f in candidates if re.search(r"q4_k_m", f["name"], re.I)), None) or \
        min(candidates, key=lambda f: f.get("size_bytes") or 0)


def annotate_listing(listing, hw, llm_hw=None, lmstudio_hw=None, runtime_hw=None, context=None, kv_per_token=None,
                     moe=None):
    """Rate files for ComfyUI and any configured Lemonade or LM Studio runtime hardware.

    runtime_hw maps an LLM server ("llamacpp", "ollama", "lemonade", "lmstudio") to the hardware of the PC it
    runs on. Each GGUF then gets "runtimes": {server: {"fit", "recommendation", "best"}}, and every GGUF gets
    "optimized_for", the server it suits best on the agent's PC (see gguf_runtime).

    context is the context length to leave room for (default 8K); kv_per_token, from the GGUF's header,
    makes that room exact; moe (the header's expert count) replaces guessing mixture-of-experts from names."""
    llm = {"context": context, "kv_per_token": kv_per_token}
    if hw:
        for f in listing["files"]:
            f["fit"] = rate(f["name"], f.get("size_bytes"), hw)
            f["recommendation"] = recommend_file(f["name"], f.get("size_bytes"), hw)
        mark_recommended(listing["files"], lambda f: variant_key(f["path"]))
    if llm_hw:
        for f in listing["files"]:
            f["llm_fit"] = rate_llm(f["name"], f.get("size_bytes"), llm_hw, **llm)
            f["llm_recommendation"] = recommend_file(f["name"], f.get("size_bytes"), llm_hw, runtime="Lemonade", **llm)
        mark_best(listing["files"], lambda f: variant_key(f["path"]), "llm_fit", "llm_best")
    if lmstudio_hw:
        for f in listing["files"]:
            f["lmstudio_fit"] = rate_llm(f["name"], f.get("size_bytes"), lmstudio_hw, "LM Studio", **llm)
        mark_best(listing["files"], lambda f: variant_key(f["path"]), "lmstudio_fit", "lmstudio_best")
    ggufs = [f for f in listing["files"] if f["name"].lower().endswith(".gguf")]
    for runtime, runtime_pc in (runtime_hw or {}).items():
        if not runtime_pc:
            continue
        label = RUNTIME_LABELS.get(runtime, runtime)
        for f in ggufs:
            f.setdefault("runtimes", {})[runtime] = {
                "fit": rate_llm(f["name"], f.get("size_bytes"), runtime_pc, label, **llm),
                "recommendation": recommend_file(f["name"], f.get("size_bytes"), runtime_pc, runtime=label, **llm)}
        groups = {}
        for f in ggufs:
            if importable_gguf(f["path"]):
                groups.setdefault(variant_key(f["path"]), []).append(f)
        for group in groups.values():
            for f in group:
                f["_fit"] = f["runtimes"][runtime]["fit"]
            pick = _pick(group, "_fit") if len(group) >= 2 else None
            for f in group:
                f.pop("_fit")
                f["runtimes"][runtime]["best"] = f is pick
    for f in ggufs:
        f["optimized_for"] = gguf_runtime(f["name"], f.get("size_bytes"), (runtime_hw or {}).get("llamacpp") or hw, moe=moe, **llm)
    return listing
