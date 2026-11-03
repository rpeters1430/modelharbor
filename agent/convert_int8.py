"""Convert a 16/32-bit safetensors model to ComfyUI's int8_convrot format.

Run with ComfyUI's own Python (it needs torch, safetensors and comfy_kitchen, which ComfyUI ships):
    python convert_int8.py SOURCE TARGET SOURCE_SHA256
The quantizer is ComfyUI's (comfy_kitchen), so the file loads exactly like the int8_convrot files ComfyUI users
share. Only linear weights inside the repeated transformer blocks are quantized; embeddings, heads and norms keep
their precision. Exits non-zero with a reason on stderr, and never touches SOURCE.
"""
import json
import re
import sys

import torch
from comfy_kitchen.backends.eager.quantization import (dequantize_int8_convrot_weight,
                                                        quantize_int8_convrot_weight)
from safetensors import safe_open
from safetensors.torch import save_file

GROUP = 256
FLOATS = (torch.float16, torch.bfloat16, torch.float32)
# blocks.12.attn.wq.weight, double_blocks.3.img_mlp.0.weight, layers.3.mlp.fc1.weight; not text_embedding.0.weight
BLOCK = re.compile(r"(?:^|[._])(?:blocks|layers)\.\d+\.")
MAX_ERROR = 0.05  # int8 row-wise error is ~1%; far above that means a broken layer


def quantizable(key, tensor, prefix):
    return (key.startswith(prefix) and key.endswith(".weight") and BLOCK.search(key) is not None
            and tensor.ndim == 2 and tensor.dtype in FLOATS and tensor.shape[1] % GROUP == 0)


def quant_conf(dtype):
    conf = {"format": "int8_tensorwise", "orig_dtype": str(dtype), "convrot": True,
            "convrot_groupsize": GROUP, "per_row": True}
    return torch.tensor(list(json.dumps(conf).encode()), dtype=torch.uint8)


def convert(source, target, source_sha):
    out, done = {}, 0
    with safe_open(source, "pt") as f:
        meta, keys = f.metadata() or {}, list(f.keys())
        # A full checkpoint also holds CLIP and VAE weights; only its diffusion model is quantized.
        prefix = "model.diffusion_model." if any(k.startswith("model.diffusion_model.") for k in keys) else ""
        for key in keys:
            tensor = f.get_tensor(key)
            if not quantizable(key, tensor, prefix):
                out[key] = tensor
                continue
            weight = tensor.float()
            q, scale = quantize_int8_convrot_weight(weight, GROUP)
            error = ((dequantize_int8_convrot_weight(q, scale, GROUP) - weight).norm() / weight.norm().clamp(min=1e-12)).item()
            if not error <= MAX_ERROR:  # also catches NaN
                raise ValueError(f"{key} would lose too much precision ({error:.3f}); not converting this model")
            name = key[:-len(".weight")]
            out[key], out[name + ".weight_scale"], out[name + ".comfy_quant"] = q, scale.float(), quant_conf(tensor.dtype)
            done += 1
    if not done:
        raise ValueError("No 16- or 32-bit transformer weights to convert; the file is already compact or not a supported model")
    meta = {**meta, "modelharbor.converted_from": source_sha, "modelharbor.format": "int8_convrot"}
    save_file(out, target, metadata=meta)
    with safe_open(target, "pt") as f:
        if set(f.keys()) != set(out):
            raise ValueError("The converted file did not read back correctly")
    return done


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit("usage: convert_int8.py SOURCE TARGET SOURCE_SHA256")
    try:
        print(json.dumps({"layers": convert(*sys.argv[1:])}))
    except (ValueError, OSError, RuntimeError) as exc:
        sys.exit(str(exc)[:300])
