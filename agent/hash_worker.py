"""Hash only specifically requested, inventoried model files; never modify them."""
import hashlib
from pathlib import Path

CHUNK = 4 * 1024 * 1024

def allowed_file(path, roots):
    target = Path(path)
    if not target.is_file() or target.is_symlink():
        raise ValueError("Missing model file or symbolic link")
    resolved = target.resolve(strict=True)
    for root in roots:
        parent = Path(root).resolve(strict=True)
        if resolved == parent:
            continue
        try:
            resolved.relative_to(parent)
            return target
        except ValueError:
            continue
    raise ValueError("Requested file is outside approved ComfyUI model roots")

def hash_requested_model(job, roots):
    target = allowed_file(job["path"], roots)
    before = target.stat()
    if (before.st_size, before.st_mtime_ns) != (job["size_bytes"],job["modified_ns"]):
        raise ValueError("File changed since the last inventory scan")
    hasher=hashlib.sha256()
    with target.open("rb") as stream:
        while True:
            chunk=stream.read(CHUNK)
            if not chunk:
                break
            hasher.update(chunk)
    after=target.stat()
    if (before.st_size,before.st_mtime_ns,before.st_ino,before.st_dev) != (
        after.st_size,after.st_mtime_ns,after.st_ino,after.st_dev):
        raise ValueError("File changed during hashing")
    return hasher.hexdigest()

def process_one(session, server, token, device, roots):
    headers={"X-ModelHarbor-Token":token}
    res=session.get(server.rstrip("/")+"/api/hash-jobs/next",params={"device":device},
                    headers=headers,timeout=20)
    res.raise_for_status()
    job=res.json().get("job")
    if not job:
        return False
    result={"device":device,"sha256":None,"error":None}
    try:
        result["sha256"]=hash_requested_model(job,roots)
    except (OSError,ValueError) as exc:
        result["error"]=str(exc)[:300]
    response=session.post(server.rstrip("/")+"/api/hash-jobs/"+job["id"]+"/result",
                          headers=headers,json=result,timeout=30)
    response.raise_for_status()
    return True
