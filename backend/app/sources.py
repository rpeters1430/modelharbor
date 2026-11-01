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
from urllib.parse import quote
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
        # NULL: found by hash lookup. 0: linked by hand to a file with a different hash.
        db.execute("ALTER TABLE civitai_files ADD COLUMN verified INTEGER")
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

def civitai_lookup(db, sha, get=fetch, now=None):
    now = now or int(time.time())
    data = None
    for host in CIVITAI_HOSTS:
        body = get(f"https://{host}/api/v1/model-versions/by-hash/{sha}")
        if body is not None:
            data = json.loads(body)
            break
    if not data or not isinstance(data.get("modelId"), int):
        db.execute("""INSERT INTO civitai_files (sha256,found,checked_at) VALUES (?,0,?)
            ON CONFLICT(sha256) DO UPDATE SET found=0,checked_at=excluded.checked_at""", (sha, now))
        return False
    model = data.get("model") or {}
    nsfw = bool(model.get("nsfw")) or int(data.get("nsfwLevel") or 0) > 3  # above PG-13
    words = [str(w)[:200] for w in data.get("trainedWords") or [] if w][:20]
    # A version can hold several files (model, text encoder, VAE); remember which one this is.
    matched = next((f for f in data.get("files") or [] if _file_sha(f) == sha), {})
    db.execute("""INSERT INTO civitai_files (sha256,found,checked_at,model_id,version_id,model_name,
        version_name,base_model,trained_words,published_at,nsfw,file_type) VALUES (?,1,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(sha256) DO UPDATE SET found=1,checked_at=excluded.checked_at,model_id=excluded.model_id,
        version_id=excluded.version_id,model_name=excluded.model_name,version_name=excluded.version_name,
        base_model=excluded.base_model,trained_words=excluded.trained_words,
        published_at=excluded.published_at,nsfw=excluded.nsfw,file_type=excluded.file_type""",
        (sha, now, data["modelId"], data.get("id"), str(model.get("name") or "")[:250],
         str(data.get("name") or "")[:250], str(data.get("baseModel") or "")[:100] or None,
         json.dumps(words), data.get("publishedAt"), int(nsfw), str(matched.get("type") or "")[:50] or None))
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

def civitai_versions(model_id, sha, get=fetch):
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
                                     "exact": bool(sha) and _file_sha(f) == sha}
                                    for f in v.get("files") or [] if isinstance(f.get("id"), int)]}
                         for v in data["modelVersions"] if isinstance(v.get("id"), int)]}

def civitai_link(db, sha, model_id, version_id, file_id, get=fetch, now=None):
    """Link a local file to a Civitai version/file chosen by the user. Exact only if the hashes match."""
    now = now or int(time.time())
    data = _civitai_model(model_id, get)
    version = next((v for v in data.get("modelVersions") or [] if v.get("id") == version_id), None)
    file = version and next((f for f in version.get("files") or [] if f.get("id") == file_id), None)
    if not file:
        return None
    verified = _file_sha(file) == sha
    words = [str(w)[:200] for w in version.get("trainedWords") or [] if w][:20]
    db.execute("""INSERT OR REPLACE INTO civitai_files (sha256,found,checked_at,model_id,version_id,model_name,
        version_name,base_model,trained_words,published_at,nsfw,file_type,verified)
        VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?)""",
        (sha, now, int(model_id), version_id, str(data.get("name") or "")[:250], str(version.get("name") or "")[:250],
         str(version.get("baseModel") or "")[:100] or None, json.dumps(words), version.get("publishedAt"),
         int(bool(data.get("nsfw"))), str(file.get("type") or "")[:50] or None, None if verified else 0))
    civitai_latest(db, model_id, get, now, data.get("modelVersions") or [])
    return {"verified": verified}

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
    shas = [r[0] for r in db.execute("""SELECT DISTINCT m.sha256 FROM models m
        LEFT JOIN civitai_files c ON c.sha256=m.sha256
        WHERE m.sha256 IS NOT NULL AND m.kind NOT LIKE 'ollama/%'
        AND (c.sha256 IS NULL OR (c.found=0 AND c.checked_at<?)) LIMIT ?""", (now - 7 * DAY, limit))]
    for sha in shas:
        civitai_lookup(db, sha, get, now)
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
        base_model,trained_words,published_at,nsfw,latest_version_id,latest_version_name,latest_published_at,verified
        FROM civitai_files WHERE found=1""")}
    checked = {r[0] for r in db.execute("SELECT sha256 FROM civitai_files")}
    ollama = dict(db.execute("SELECT name,remote_digest FROM ollama_remote WHERE remote_digest IS NOT NULL"))
    hf = {(r[0], r[1]): r for r in db.execute("""SELECT s.device,s.path,s.repository,s.remote_path,s.sha256,s.latest_sha
        FROM selected_matches s JOIN models m ON s.device=m.device AND s.path=m.path
        AND s.local_size=m.size_bytes AND s.local_modified_ns=m.modified_ns""")}
    for record in records:
        record["source"] = record["update"] = None
        record["source_checked"] = record.get("sha256") in checked
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
             nsfw, latest_id, latest_name, latest_published, verified) = row
            record["source"] = {"provider": "civitai", "model_id": model_id, "version_id": version_id,
                "model_name": model_name, "version_name": version_name, "base_model": base,
                "trained_words": json.loads(words or "[]"), "nsfw": bool(nsfw), "verified": verified != 0,
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
