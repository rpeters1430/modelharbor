"""Conservative, read-only inventory enrichment. No network calls or file hashing."""
import re
from collections import defaultdict

def physical_device(source):
    return source.rsplit(":", 1)[0] if source.endswith((":comfyui", ":ollama", ":lemonade")) else source

PATTERNS = (
    (r"krea.?2", "Krea 2"),  # before FLUX, which "Flux.1 Krea" belongs to
    (r"z.?image", "Z-Image"), (r"qwen", "Qwen"), (r"wan(?:2|_|-|\b)", "Wan"),
    (r"flux|flux1", "FLUX"), (r"illustrious|noobai", "Illustrious"),
    (r"pony", "Pony"), (r"sdxl|juggernautxl|realvisxl", "SDXL"),
    (r"sd15|sd ?1[._-]?5|stable.?diffusion.?1[._-]?5", "SD 1.5"),
    (r"sd3|sd ?3(\.5)?|stable.?diffusion.?3", "SD 3"),
    (r"moss", "MOSS"), (r"hunyuan", "Hunyuan"), (r"ltx", "LTX"),
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
    base = (record.get("source") or {}).get("base_model") or (record.get("local_meta") or {}).get("base_model") or record.get("base_model_guess") or ""
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
    sources = {}  # file key -> the source linked to any one of its names
    for index, record in enumerate(records):
        # Same device + file id is one file on disk under several paths (hard link).
        file_key = (physical_device(record["device"]), record.get("file_id")) if record.get("file_id") else None
        if file_key in seen_files:
            hard_links.add(index)
        elif file_key:
            seen_files.add(file_key)
        if file_key and record.get("source") and file_key not in sources:
            sources[file_key] = (record["source"], record.get("update"))
        if record["size_bytes"] > 0 and not record["kind"].startswith(("ollama/", "lemonade/")):
            groups[_dup_key(record)].add(file_key or index)
    result = []
    for index, record in enumerate(records):
        file_key = (physical_device(record["device"]), record.get("file_id")) if record.get("file_id") else None
        if not record.get("source") and file_key in sources:
            # Another name of the same file is linked; so is this one.
            record = {**record, "source": sources[file_key][0], "update": sources[file_key][1]}
        label, verified = family(record)
        copies = len(groups.get(_dup_key(record), ()))
        result.append({**record, "physical_device": physical_device(record["device"]),
            "family_guess": label, "family_verified": verified,
            "hard_link": index in hard_links,
            "possible_duplicate": copies > 1,
            # Same SHA-256 is proof of identical bytes; name + size is only a hint.
            "duplicate_verified": copies > 1 and bool(record.get("sha256")),
            "duplicate_group_size": copies if copies > 1 else 0})
    return result

def _dup_key(record):
    """Hashed files are duplicates only when their SHA-256 matches; unhashed ones by name and size."""
    if record.get("sha256"):
        return ("sha", record["sha256"])
    return (record["name"].casefold(), record["size_bytes"])
