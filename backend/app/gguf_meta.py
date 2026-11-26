"""Read an LLM's shape from the start of its GGUF file, to estimate the memory its context needs.

A GGUF file starts with key/value metadata (architecture, layer and attention-head counts) before the
tensors, so the first couple of megabytes are enough; the rest of the file is never downloaded. The
architecture keys come before the tokenizer's large arrays, so a truncated read still finds them."""
import struct
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

HEADER_BYTES = 2 * 1024 * 1024
CACHE_TTL = 24 * 3600
_cache = {}

# GGUF value types: struct format and size of the fixed-size ones; 8 is a string, 9 an array.
_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
STRING, ARRAY = 8, 9
WANTED = ("block_count", "context_length", "embedding_length", "attention.head_count", "attention.head_count_kv",
          "attention.key_length", "attention.value_length", "attention.sliding_window", "expert_count")


class _Truncated(Exception):
    pass


class _Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, n):
        if self.pos + n > len(self.data):
            raise _Truncated
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def unpack(self, fmt):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self):
        return self.take(self.unpack("<Q")).decode("utf-8", "replace")

    def value(self, kind, keep=True):
        if kind in _SCALARS:
            return self.unpack(_SCALARS[kind])
        if kind == STRING:
            return self.string()
        if kind == ARRAY:
            item, count = self.unpack("<I"), self.unpack("<Q")
            if item in _SCALARS and not keep:  # skip large numeric arrays without decoding them
                self.take(struct.calcsize(_SCALARS[item]) * count)
                return None
            values = [self.value(item, keep) for _ in range(count)]
            return values if keep else None
        raise ValueError(f"Unknown GGUF value type {kind}")


def parse_header(data):
    """{"architecture", "block_count", "attention.head_count_kv", ...} from the start of a GGUF file.
    Returns what it found before the data ran out; None if it isn't a GGUF."""
    reader = _Reader(data)
    try:
        if reader.take(4) != b"GGUF":
            return None
        if reader.unpack("<I") < 2:
            return None  # version 1 used 32-bit counts; long obsolete
        reader.unpack("<Q")  # tensor count
        count = reader.unpack("<Q")
    except _Truncated:
        return None
    meta, arch = {}, None
    try:
        for _ in range(count):
            key = reader.string()
            kind = reader.unpack("<I")
            short = key.split(".", 1)[1] if arch and key.startswith(arch + ".") else None
            keep = key == "general.architecture" or short in WANTED
            value = reader.value(kind, keep)
            if key == "general.architecture":
                arch = meta["architecture"] = str(value)
            elif short in WANTED:
                meta[short] = value
    except (_Truncated, ValueError, UnicodeDecodeError, struct.error):
        pass
    return meta or None


def _per_layer(value, layers):
    """A head count is one number, or a list with one per layer (models that vary it by layer)."""
    if isinstance(value, list):
        return [int(v or 0) for v in value[:layers]] + [0] * max(0, layers - len(value))
    return [int(value or 0)] * layers


def kv_bytes_per_token(meta):
    """Bytes of f16 K and V cache one token of context takes across all layers, or None if the header
    lacks the numbers. Models with sliding-window layers need less; this errs on the side of more."""
    try:
        layers, heads = int(meta["block_count"]), meta["attention.head_count"]
        width = int(meta["embedding_length"])
    except (KeyError, TypeError, ValueError):
        return None
    kv_heads = meta.get("attention.head_count_kv", heads)
    head_counts = _per_layer(heads, layers)
    first = next((h for h in head_counts if h), 0)
    if not layers or not first:
        return None
    key_len = int(meta.get("attention.key_length") or width // first)
    value_len = int(meta.get("attention.value_length") or key_len)
    return sum(kv * (key_len + value_len) * 2 for kv in _per_layer(kv_heads, layers)) or None


def summary(meta):
    """What the dashboard shows and the fit uses: architecture, trained context, KV bytes per token."""
    if not meta:
        return None
    return {"architecture": meta.get("architecture"), "context_length": meta.get("context_length"),
            "layers": meta.get("block_count"), "experts": meta.get("expert_count"),
            "kv_bytes_per_token": kv_bytes_per_token(meta)}


def fetch_range(url, size=HEADER_BYTES):
    """The first size bytes of a file. Hugging Face honours Range; a server that ignores it is cut off."""
    request = Request(url, headers={"Range": f"bytes=0-{size - 1}", "User-Agent": "ModelHarbor/0.4 (gguf header)"})
    with urlopen(request, timeout=20) as response:
        return response.read(size)


def read_header(repo, path, get=None):
    """summary() for a GGUF file in a Hugging Face repository, cached for a day; None when unreadable."""
    key = (repo, path)
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_TTL:
        return cached[1]
    url = "https://huggingface.co/" + quote(repo, safe="/") + "/resolve/main/" + quote(path, safe="/")
    try:
        result = summary(parse_header((get or fetch_range)(url)))
    except (OSError, ValueError):
        result = None
    _cache[key] = (time.monotonic(), result)
    return result
