"""Conservative, read-only inventory enrichment. No network calls or file hashing."""
import re
from collections import defaultdict

def physical_device(source):
    return source.rsplit(":", 1)[0] if source.endswith((":comfyui", ":ollama")) else source

PATTERNS = (
    (r"qwen", "Qwen"), (r"wan(?:2|_|-|\b)", "Wan"),
    (r"flux|flux1", "FLUX"), (r"illustrious|noobai", "Illustrious"),
    (r"pony", "Pony"), (r"sdxl|juggernautxl|realvisxl", "SDXL"),
    (r"sd15|sd ?1[._-]?5|stable.?diffusion.?1[._-]?5", "SD 1.5"),
)

def _label(text):
    for pattern, label in PATTERNS:
        if re.search(pattern, text.lower()):
            return label
    return None

def family(record):
    """Best evidence first: Civitai base model, then the family folder, then the filename."""
    kind = (record.get("kind") or "").lower()
    if kind.startswith("ollama/"):
        return kind.split("/", 1)[1] or "unknown", False
    base = (record.get("source") or {}).get("base_model") or ""
    if base:
        return _label(base) or base, True
    for text in (*re.split(r"[\\/]", record.get("path") or "")[-3:-1], record.get("name") or ""):
        if label := _label(text):
            return label, False
    return "Unknown", False

def enrich(records):
    groups = defaultdict(set)
    seen_files = set()
    hard_links = set()
    for index, record in enumerate(records):
        # Same device + file id is one file on disk under several paths (hard link).
        file_key = (physical_device(record["device"]), record.get("file_id")) if record.get("file_id") else None
        if file_key in seen_files:
            hard_links.add(index)
        elif file_key:
            seen_files.add(file_key)
        # Filename + size is only a *possible* duplicate, not proof of same bytes.
        key = (record["name"].casefold(), record["size_bytes"])
        if record["size_bytes"] > 0 and not record["kind"].startswith("ollama/"):
            groups[key].add(file_key or index)
    result = []
    for index, record in enumerate(records):
        label, verified = family(record)
        copies = len(groups.get((record["name"].casefold(), record["size_bytes"]), ()))
        result.append({**record, "physical_device": physical_device(record["device"]),
            "family_guess": label, "family_verified": verified,
            "hard_link": index in hard_links,
            "possible_duplicate": copies > 1,
            "duplicate_group_size": copies if copies > 1 else 0})
    return result
