"""Exact source identification and update checks. Metadata only; never downloads model files.

- Civitai: look up each local SHA-256, then look for a higher-numbered version of the same variant.
- Ollama: the local digest is the SHA-256 of the registry manifest, so a different manifest means an update.
- Hugging Face: user-selected matches are rechecked against the file's current LFS SHA-256.
"""
import hashlib
import json
import logging
import re
import threading
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

LOG = logging.getLogger("modelharbor.sources")
# Both domains serve the same API; civitai.red is the mature-content site.
CIVITAI_HOSTS = ("civitai.com", "civitai.red")
OLLAMA_NAME = re.compile(r"^(?:([a-z0-9][a-z0-9._-]{0,63})/)?([a-z0-9][a-z0-9._-]{0,127}):([A-Za-z0-9][A-Za-z0-9._-]{0,127})$")
DAY = 86400
MAX_BYTES = 3_000_000

def ensure_tables(db):
    db.execute("""CREATE TABLE IF NOT EXISTS civitai_files (
        sha256 TEXT PRIMARY KEY, found INTEGER NOT NULL, checked_at INTEGER NOT NULL,
        model_id INTEGER, version_id INTEGER, model_name TEXT, version_name TEXT,
        base_model TEXT, trained_words TEXT, published_at TEXT, nsfw INTEGER,
        latest_version_id INTEGER, latest_version_name TEXT, latest_published_at TEXT,
        latest_checked_at INTEGER)""")
    columns = [row[1] for row in db.execute("PRAGMA table_info(civitai_files)")]
    for column in ("file_type", "latest_file"):
        if column not in columns:
            db.execute(f"ALTER TABLE civitai_files ADD COLUMN {column} TEXT")
    if "verified" not in columns:
        # NULL: exact hash. 2: same tensor data (AutoV3), only the embedded metadata differs.
        # 0: linked by hand to a file with different contents (a conversion, for example).
        db.execute("ALTER TABLE civitai_files ADD COLUMN verified INTEGER")
    if "weights_checked" not in columns:
        # Set once the weights-only hash was tried too, so files get that lookup as soon as it is known.
        db.execute("ALTER TABLE civitai_files ADD COLUMN weights_checked INTEGER")
    if "lora_weight" not in columns:
        # Median LoRA strength in the version's example images; weight_checked marks rows already looked at.
        db.execute("ALTER TABLE civitai_files ADD COLUMN lora_weight REAL")
        db.execute("ALTER TABLE civitai_files ADD COLUMN weight_checked INTEGER")
    # Files the user marked as not on any site (own LoRAs, merges), so they leave the Unmatched list.
    db.execute("CREATE TABLE IF NOT EXISTS match_ignored (sha256 TEXT PRIMARY KEY, ignored_at INTEGER NOT NULL)")
    db.execute("""CREATE TABLE IF NOT EXISTS ollama_remote (
        name TEXT PRIMARY KEY, remote_digest TEXT, checked_at INTEGER NOT NULL)""")

def fetch(url, accept="application/json"):
    """Return the body, or None for 404. Only called with URLs built from fixed hosts."""
    request = Request(url, headers={"Accept": accept, "User-Agent": "ModelHarbor/0.4 (update checks)"})
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read(MAX_BYTES + 1)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    if len(body) > MAX_BYTES:
        raise ValueError("Response too large: " + url)
    return body

def civitai_url(model_id, version_id=None, nsfw=False):
    url = f"https://{CIVITAI_HOSTS[1] if nsfw else CIVITAI_HOSTS[0]}/models/{int(model_id)}"
    return url + (f"?modelVersionId={int(version_id)}" if version_id else "")

def _by_hash(digest, get):
    for host in CIVITAI_HOSTS:
        body = get(f"https://{host}/api/v1/model-versions/by-hash/{digest}")
        if body is not None:
            data = json.loads(body)
            return data if isinstance(data.get("modelId"), int) else None
    return None

def _autov3(item):
    return str((item.get("hashes") or {}).get("AutoV3") or "").lower()

def _key(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())

def example_weight(version):
    """Median strength this LoRA was used at in the version's example images, or None.

    Images credit resources either by version id (civitaiResources) or by the name the creator's UI used
    (resources, or a <lora:name:weight> tag in the prompt), so names are matched loosely against the files."""
    names = {_key(f.get("name", "").rsplit(".", 1)[0]) for f in version.get("files") or []}
    names.add(_key((version.get("model") or {}).get("name", "")))
    names.discard("")
    found = []
    for image in version.get("images") or []:
        meta = image.get("meta") if isinstance(image, dict) else None
        if not isinstance(meta, dict):
            continue
        weight = next((r.get("weight") for r in meta.get("civitaiResources") or []
                       if isinstance(r, dict) and r.get("modelVersionId") == version.get("id")), None)
        loras = [r for r in meta.get("resources") or [] if isinstance(r, dict) and r.get("type") == "lora"]
        if weight is None:
            weight = next((r.get("weight") for r in loras if _key(r.get("name")) in names), None)
        if weight is None:
            tags = re.findall(r"<lora:([^:>]+):([0-9.]+)>", str(meta.get("prompt") or ""))
            weight = next((w for n, w in tags if _key(n) in names), None)
        if weight is None and len(loras) == 1:
            weight = loras[0].get("weight")  # the only LoRA in the image is this one
        try:
            weight = float(weight)
        except (TypeError, ValueError):
            continue
        if 0 < weight <= 2:
            found.append(weight)
    if not found:
        return None
    found.sort()
    middle = len(found) // 2
    median = found[middle] if len(found) % 2 else (found[middle - 1] + found[middle]) / 2
    return round(median * 20) / 20

def civitai_lookup(db, sha, get=fetch, now=None, weights=None):
    now = now or int(time.time())
    data, verified = _by_hash(sha, get), None
    matched = data and next((f for f in data.get("files") or [] if _file_sha(f) == sha), {})
    if not data and weights:
        # Same tensors with edited metadata: Civitai looks up its AutoV3 hash, the first 12 characters.
        data, verified = _by_hash(weights[:12], get), 2
        matched = data and next((f for f in data.get("files") or [] if _autov3(f) == weights[:12]), None)
        if not matched:
            data = None
    if not data:
        db.execute("""INSERT INTO civitai_files (sha256,found,checked_at,weights_checked) VALUES (?,0,?,?)
            ON CONFLICT(sha256) DO UPDATE SET found=0,checked_at=excluded.checked_at,
            weights_checked=excluded.weights_checked""", (sha, now, 1 if weights else None))
        return False
    model = data.get("model") or {}
    nsfw = bool(model.get("nsfw")) or int(data.get("nsfwLevel") or 0) > 3  # above PG-13
    words = [str(w)[:200] for w in data.get("trainedWords") or [] if w][:20]
    # A version can hold several files (model, text encoder, VAE); `matched` is the one this is.
    db.execute("""INSERT INTO civitai_files (sha256,found,checked_at,model_id,version_id,model_name,
        version_name,base_model,trained_words,published_at,nsfw,file_type,verified,lora_weight,weight_checked)
        VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?,?,1)
        ON CONFLICT(sha256) DO UPDATE SET found=1,checked_at=excluded.checked_at,model_id=excluded.model_id,
        version_id=excluded.version_id,model_name=excluded.model_name,version_name=excluded.version_name,
        base_model=excluded.base_model,trained_words=excluded.trained_words,
        published_at=excluded.published_at,nsfw=excluded.nsfw,file_type=excluded.file_type,verified=excluded.verified,
        lora_weight=excluded.lora_weight,weight_checked=1""",
        (sha, now, data["modelId"], data.get("id"), str(model.get("name") or "")[:250],
         str(data.get("name") or "")[:250], str(data.get("baseModel") or "")[:100] or None,
         json.dumps(words), data.get("publishedAt"), int(nsfw), str(matched.get("type") or "")[:50] or None, verified,
         example_weight(data)))
    return True

# Civitai labels the main weights inconsistently between versions of one model.
MAIN_FILE_TYPES = {"Model", "UNet", "Diffusion Model", "Checkpoint", "Pruned Model"}

def _file_sha(item):
    digest = str(((item or {}).get("hashes") or {}).get("SHA256") or "").lower()
    return digest if re.fullmatch(r"[0-9a-f]{64}", digest) else None

def _file_group(file_type):
    return "main" if file_type in MAIN_FILE_TYPES else file_type

def matching_file(version, file_type):
    """The file in a newer version that replaces the local one, preferring the primary file."""
    if not file_type:
        return None  # Civitai didn't say which of its files ours is, so don't guess a replacement
    files = [f for f in version.get("files") or [] if _file_group(f.get("type")) == _file_group(file_type)
             and _file_sha(f) and isinstance(f.get("id"), int)]
    return max(files, key=lambda f: bool(f.get("primary")), default=None)

VERSION = re.compile(r"(?<![a-z0-9])v?(\d+(?:\.\d+)+)|(?<![a-z0-9])v(\d+)(?![a-z0-9])")

def version_key(name):
    """Split a version name into (label without version numbers, version tuple)."""
    text = (name or "").lower()
    match = VERSION.search(text)
    if not match:
        return None
    number = tuple(int(n) for n in (match.group(1) or match.group(2)).split("."))
    label = re.sub(r"[^a-z0-9]+", " ", VERSION.sub(" ", text)).strip()
    return label, number

def newer_version(local_name, base, versions, file_type=None):
    """Newest version that is the same variant with a higher number, e.g. "Turbo v1.0" -> "Turbo v3.0".

    Civitai also lists variants (inpainting, DMD, INT8, high/low noise, other base models) as
    versions, so the newest upload is not necessarily an update. The newer version must also
    contain the same kind of file (a text encoder is not replaced by a diffusion model).
    """
    local = version_key(local_name)
    if not local:
        return None
    candidates = []
    for v in versions:
        key = version_key(v.get("name"))
        if (key and key[0] == local[0] and key[1] > local[1] and v.get("baseModel") == base
                and v.get("status", "Published") == "Published" and matching_file(v, file_type)):
            candidates.append(v)
    # Numbering is inconsistent (V0.25 vs V0.4), so the latest upload wins among real successors.
    return max(candidates, key=lambda v: (v.get("publishedAt") or "", version_key(v["name"])[1]), default=None)

def _civitai_model(model_id, get):
    body = get(f"https://{CIVITAI_HOSTS[0]}/api/v1/models/{int(model_id)}")
    return (json.loads(body) or {}) if body else {}

VERDICT_RANK = {"exact": 0, "same_weights": 1, "same_size": 2}

def _verdict(item, sha, weights, size):
    """Whether a Civitai file is the local file: identical, same tensors, or only the same size."""
    if sha and _file_sha(item) == sha:
        return "exact"
    if weights and _autov3(item) == weights[:12]:
        return "same_weights"
    file_size = int(float(item.get("sizeKB") or 0) * 1024)
    return "same_size" if size and abs(file_size - size) <= 1024 else None  # sizeKB is rounded

def civitai_check(query, sha, weights, size, get=fetch):
    """Search Civitai and judge every file in the results against the local file."""
    body = get(f"https://{CIVITAI_HOSTS[0]}/api/v1/models?" + urlencode({"query": query, "limit": 20, "nsfw": "true"}))
    data = json.loads(body) if body else {}
    models, checked = [], 0
    for m in data.get("items") or []:
        if not isinstance(m.get("id"), int):
            continue
        files, sizes = [], set()
        for v in m.get("modelVersions") or []:
            for f in v.get("files") or []:
                if not isinstance(f.get("id"), int):
                    continue
                checked += 1
                sizes.add(int(float(f.get("sizeKB") or 0) * 1024))
                verdict = _verdict(f, sha, weights, size)
                if verdict:
                    files.append({"verdict": verdict, "version_id": v.get("id"), "version_name": str(v.get("name") or "")[:250],
                                  "base_model": v.get("baseModel"), "file_id": f["id"], "file_name": str(f.get("name") or "")[:200]})
        files.sort(key=lambda f: VERDICT_RANK[f["verdict"]])
        models.append({"id": m["id"], "name": str(m.get("name") or "")[:250], "type": m.get("type"),
                       "url": civitai_url(m["id"], None, bool(m.get("nsfw"))), "best": files[0]["verdict"] if files else None,
                       "files": files, "sizes": sorted(sizes)[:6]})
    models.sort(key=lambda m: VERDICT_RANK.get(m["best"], 9))
    return {"checked_files": checked, "models": models}

def civitai_versions(model_id, sha, get=fetch, weights=None):
    """A model's versions and files for the link picker, marking files whose hash equals ours."""
    data = _civitai_model(model_id, get)
    if not data.get("modelVersions"):
        return None
    nsfw = bool(data.get("nsfw"))
    return {"id": int(model_id), "name": str(data.get("name") or "")[:250], "url": civitai_url(model_id, None, nsfw),
            "versions": [{"id": v.get("id"), "name": str(v.get("name") or "")[:250], "base_model": v.get("baseModel"),
                          "published_at": v.get("publishedAt"),
                          "files": [{"id": f.get("id"), "name": str(f.get("name") or "")[:200], "type": f.get("type"),
                                     "primary": bool(f.get("primary")), "size_bytes": int(float(f.get("sizeKB") or 0) * 1024),
                                     "exact": bool(sha) and _file_sha(f) == sha,
                                     "same_weights": bool(weights) and _autov3(f) == weights[:12]}
                                    for f in v.get("files") or [] if isinstance(f.get("id"), int)]}
                         for v in data["modelVersions"] if isinstance(v.get("id"), int)]}

def civitai_link(db, sha, model_id, version_id, file_id, get=fetch, now=None, weights=None):
    """Link a local file to a Civitai version/file chosen by the user. Verified if the file or its tensors match."""
    now = now or int(time.time())
    data = _civitai_model(model_id, get)
    version = next((v for v in data.get("modelVersions") or [] if v.get("id") == version_id), None)
    file = version and next((f for f in version.get("files") or [] if f.get("id") == file_id), None)
    if not file:
        return None
    verified = None if _file_sha(file) == sha else 2 if weights and _autov3(file) == weights[:12] else 0
    words = [str(w)[:200] for w in version.get("trainedWords") or [] if w][:20]
    db.execute("""INSERT OR REPLACE INTO civitai_files (sha256,found,checked_at,model_id,version_id,model_name,
        version_name,base_model,trained_words,published_at,nsfw,file_type,verified,lora_weight,weight_checked)
        VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
        (sha, now, int(model_id), version_id, str(data.get("name") or "")[:250], str(version.get("name") or "")[:250],
         str(version.get("baseModel") or "")[:100] or None, json.dumps(words), version.get("publishedAt"),
         int(bool(data.get("nsfw"))), str(file.get("type") or "")[:50] or None, verified,
         example_weight({**version, "model": {"name": data.get("name")}})))
    civitai_latest(db, model_id, get, now, data.get("modelVersions") or [])
    return {"verified": verified != 0, "same_weights": verified == 2}

def civitai_unlink(db, sha):
    # Forget the link; the next pass looks the file up by hash again.
    db.execute("DELETE FROM civitai_files WHERE sha256=?", (sha,))

def civitai_latest(db, model_id, get=fetch, now=None, versions=None):
    """Record the newer version, if any, for every local file of this model."""
    now = now or int(time.time())
    if versions is None:
        versions = _civitai_model(model_id, get).get("modelVersions", [])
    rows = db.execute("""SELECT sha256,base_model,version_name,file_type FROM civitai_files
        WHERE model_id=? AND found=1""", (model_id,)).fetchall()
    for sha, base, name, file_type in rows:
        latest = newer_version(name, base, versions, file_type)
        item = latest and matching_file(latest, file_type)
        # Exactly what the agent should download and the hash it must have.
        file = item and json.dumps({"id": item["id"], "name": str(item.get("name") or "")[:200],
            "sha256": _file_sha(item), "size_bytes": int(float(item.get("sizeKB") or 0) * 1024)})
        db.execute("""UPDATE civitai_files SET latest_version_id=?,latest_version_name=?,
            latest_published_at=?,latest_checked_at=?,latest_file=? WHERE sha256=?""",
            (latest and latest.get("id"), latest and str(latest.get("name") or "")[:250],
             latest and latest.get("publishedAt"), now, file, sha))

def ollama_check(db, name, get=fetch, now=None):
    now = now or int(time.time())
    match = OLLAMA_NAME.fullmatch(name if ":" in name else name + ":latest")
    if match:
        namespace, model, tag = match.groups()
        body = get(f"https://registry.ollama.ai/v2/{namespace or 'library'}/{model}/manifests/{tag}",
                   "application/vnd.docker.distribution.manifest.v2+json")
        remote = hashlib.sha256(body).hexdigest() if body is not None else None
    else:
        remote = None  # pulled from another registry (e.g. huggingface.co/...): not checked
    db.execute("""INSERT INTO ollama_remote VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET
        remote_digest=excluded.remote_digest,checked_at=excluded.checked_at""", (name, remote, now))

def huggingface_check(db, list_files, now=None):
    now = now or int(time.time())
    rows = db.execute("""SELECT device,path,repository,remote_path FROM selected_matches
        WHERE latest_checked_at IS NULL OR latest_checked_at<?""", (now - DAY,)).fetchall()
    for device, path, repo, remote_path in rows:
        try:
            files = list_files(repo)["files"]
        except Exception as exc:  # provider errors must not stop the other checks
            LOG.warning("Hugging Face check failed for %s: %s", repo, exc)
            continue
        latest = next((f["sha256"] for f in files if f["path"] == remote_path), None)
        db.execute("UPDATE selected_matches SET latest_sha=?,latest_checked_at=? WHERE device=? AND path=?",
                   (latest, now, device, path))
        db.commit()

def run_pass(db, get=fetch, list_files=None, now=None, pause=0.5, limit=300):
    """One bounded round of lookups. Each write is committed so progress survives restarts."""
    now = now or int(time.time())
    pending = db.execute("""SELECT m.sha256, MAX(m.weights_sha256) FROM models m
        LEFT JOIN civitai_files c ON c.sha256=m.sha256
        WHERE m.sha256 IS NOT NULL AND m.kind NOT LIKE 'ollama/%'
        AND (c.sha256 IS NULL OR (c.found=0 AND (c.checked_at<?
             OR (m.weights_sha256 IS NOT NULL AND c.weights_checked IS NULL))))
        GROUP BY m.sha256 LIMIT ?""", (now - 7 * DAY, limit)).fetchall()
    for sha, weights in pending:
        civitai_lookup(db, sha, get, now, weights)
        db.commit()
        time.sleep(pause)
    # LoRAs identified before example weights were recorded: read their version once.
    backfill = db.execute("""SELECT DISTINCT c.sha256, c.version_id FROM civitai_files c JOIN models m ON m.sha256=c.sha256
        WHERE c.found=1 AND c.weight_checked IS NULL AND c.version_id IS NOT NULL AND m.kind LIKE '%lora%'
        LIMIT ?""", (limit,)).fetchall()
    for sha, version_id in backfill:
        body = next((b for b in (get(f"https://{h}/api/v1/model-versions/{int(version_id)}") for h in CIVITAI_HOSTS)
                     if b is not None), None)
        weight = example_weight(json.loads(body)) if body else None
        db.execute("UPDATE civitai_files SET lora_weight=?,weight_checked=1 WHERE sha256=?", (weight, sha))
        db.commit()
        time.sleep(pause)
    model_ids = [r[0] for r in db.execute("""SELECT DISTINCT model_id FROM civitai_files WHERE found=1
        AND (latest_checked_at IS NULL OR latest_checked_at<?) LIMIT ?""", (now - DAY, limit))]
    for model_id in model_ids:
        civitai_latest(db, model_id, get, now)
        db.commit()
        time.sleep(pause)
    names = [r[0] for r in db.execute("""SELECT DISTINCT m.name FROM models m
        LEFT JOIN ollama_remote o ON o.name=m.name WHERE m.kind LIKE 'ollama/%'
        AND (o.name IS NULL OR o.checked_at<?) LIMIT ?""", (now - DAY, limit))]
    for name in names:
        ollama_check(db, name, get, now)
        db.commit()
        time.sleep(pause)
    if list_files:
        huggingface_check(db, list_files, now)

def start_worker(connect, list_files, interval=300):
    def loop():
        while True:
            try:
                db = connect()
                try:
                    run_pass(db, list_files=list_files)
                finally:
                    db.close()
            except Exception as exc:  # network or provider errors: retry next round
                LOG.warning("Source check pass failed: %s", exc)
            time.sleep(interval)
    threading.Thread(target=loop, name="source-checks", daemon=True).start()

def _ollama_page(name):
    base = name.split(":")[0]
    return "https://ollama.com/" + quote(base if "/" in base else "library/" + base, safe="/")

def annotate(db, records):
    """Attach source identity and update availability to inventory records."""
    civitai = {r[0]: r for r in db.execute("""SELECT sha256,model_id,version_id,model_name,version_name,
        base_model,trained_words,published_at,nsfw,latest_version_id,latest_version_name,latest_published_at,verified,
        lora_weight FROM civitai_files WHERE found=1""")}
    checked = {r[0] for r in db.execute("SELECT sha256 FROM civitai_files")}
    ignored = {r[0] for r in db.execute("SELECT sha256 FROM match_ignored")}
    ollama = dict(db.execute("SELECT name,remote_digest FROM ollama_remote WHERE remote_digest IS NOT NULL"))
    hf = {(r[0], r[1]): r for r in db.execute("""SELECT s.device,s.path,s.repository,s.remote_path,s.sha256,s.latest_sha
        FROM selected_matches s JOIN models m ON s.device=m.device AND s.path=m.path
        AND s.local_size=m.size_bytes AND s.local_modified_ns=m.modified_ns""")}
    for record in records:
        record["source"] = record["update"] = None
        record["source_checked"] = record.get("sha256") in checked
        record["match_ignored"] = record.get("sha256") in ignored
        sha = record.get("sha256")
        if record["kind"].startswith("ollama/"):
            remote = ollama.get(record["name"])
            if sha and remote:
                url = _ollama_page(record["name"])
                record["source"] = {"provider": "ollama", "model_name": record["name"], "url": url}
                if remote != sha:
                    record["update"] = {"provider": "ollama", "version_name": "Newer build", "url": url}
            continue
        row = civitai.get(sha)
        if row:
            (_, model_id, version_id, model_name, version_name, base, words, published,
             nsfw, latest_id, latest_name, latest_published, verified, weight) = row
            record["source"] = {"provider": "civitai", "model_id": model_id, "version_id": version_id,
                "model_name": model_name, "version_name": version_name, "base_model": base,
                "trained_words": json.loads(words or "[]"), "nsfw": bool(nsfw), "verified": verified != 0,
                "weights_match": verified == 2, "weight": weight,
                "url": civitai_url(model_id, version_id, bool(nsfw))}
            if latest_id and latest_id != version_id:
                record["update"] = {"provider": "civitai", "version_id": latest_id, "version_name": latest_name,
                                    "url": civitai_url(model_id, latest_id, bool(nsfw))}
            continue
        match = hf.get((record["device"], record["path"]))
        if match:
            _, _, repo, remote_path, digest, latest = match
            url = "https://huggingface.co/" + quote(repo, safe="/") + "/blob/main/" + quote(remote_path, safe="/")
            record["source"] = {"provider": "huggingface", "model_name": repo, "version_name": remote_path, "url": url}
            if latest and latest != digest:
                record["update"] = {"provider": "huggingface", "version_name": "File changed on main", "url": url}
    return records
