"""Read-only inventory collection for LM Studio's native v1 REST API."""
import os
import socket
from urllib.parse import urlsplit

import requests


def auth_headers():
    """Read the optional LM Studio API key from the Windows user environment."""
    key = os.environ.get("LMSTUDIO_API_KEY")
    return {"Authorization": "Bearer " + key} if key else {}


def _api_root(base_url):
    parsed = urlsplit((base_url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("LM Studio URL must be an http(s) endpoint without embedded credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("LM Studio URL must not contain a query or fragment")
    return base_url.rstrip("/") + "/api/v1/"


def _raise_for_status(response):
    if response.status_code in (401, 403):
        raise ValueError("LM Studio refused the request; set LMSTUDIO_API_KEY in the Windows user environment")
    response.raise_for_status()


def endpoint_info(base_url):
    parsed = urlsplit(base_url)
    host = parsed.hostname or ""
    local = host.casefold() in {"localhost", "127.0.0.1", "::1", socket.gethostname().casefold()}
    return {"host": host[:253], "local": local}


def collect(session, base_url, device):
    """Return downloaded LM Studio models, preserving the model key and loaded-instance metadata."""
    response = session.get(_api_root(base_url) + "models", headers=auth_headers(), timeout=20)
    _raise_for_status(response)
    body = response.json()
    if not isinstance(body, dict) or not isinstance(body.get("models"), list):
        raise ValueError("LM Studio returned an unsupported model-list response")

    records = []
    malformed = 0
    for item in body["models"]:
        if not isinstance(item, dict):
            malformed += 1
            continue
        key = item.get("key")
        if not isinstance(key, str) or not key.strip() or len(key) > 1024:
            malformed += 1
            continue
        try:
            size = max(0, int(item.get("size_bytes") or 0))
        except (TypeError, ValueError, OverflowError):
            size = 0
        model_type = item.get("type") if item.get("type") in ("llm", "embedding", "decision") else "unknown"
        loaded = item.get("loaded_instances")
        if not isinstance(loaded, list):
            loaded = []
        instances = []
        for instance in loaded:
            if not isinstance(instance, dict) or not isinstance(instance.get("id"), str):
                continue
            config = instance.get("config") if isinstance(instance.get("config"), dict) else {}
            instances.append({"id": instance["id"][:512], "context_length": config.get("context_length")})
        quantization = item.get("quantization")
        if isinstance(quantization, dict):
            quantization = {k: quantization.get(k) for k in ("name", "bits_per_weight")}
        else:
            quantization = None
        runtime_meta = {
            "runtime": "lmstudio",
            "model_key": key,
            "type": model_type,
            "architecture": item.get("architecture"),
            "quantization": quantization,
            "params_string": item.get("params_string"),
            "format": item.get("format"),
            "max_context_length": item.get("max_context_length"),
            "capabilities": item.get("capabilities") if isinstance(item.get("capabilities"), dict) else None,
            "loaded_instances": instances,
        }
        records.append({
            "device": device,
            "path": "lmstudio://" + key,
            "name": str(item.get("display_name") or key)[:512],
            "kind": "lmstudio/" + model_type,
            "size_bytes": size,
            "modified_ns": 0,
            "file_id": None,
            "sha256": None,
            "checkpoint": key[:1024],
            "base_model_guess": str(item.get("architecture") or "")[:100] or None,
            "meta_title": str(item.get("display_name") or "")[:250] or None,
            "runtime_meta": runtime_meta,
        })
    if not records and malformed:
        raise ValueError("LM Studio returned no usable model records")
    return records

