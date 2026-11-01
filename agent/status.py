"""Best-effort progress reporting to the dashboard, plus a lock so only one sync runs at a time."""
import logging
import os
import time
from pathlib import Path
import requests

LOG = logging.getLogger("modelharbor")
STALE_LOCK_SECONDS = 4 * 3600

class Reporter:
    def __init__(self, session, server, token, device, interval=3.0):
        self.session, self.url, self.device = session, server.rstrip("/") + "/api/agents/status", device
        self.headers = {"X-ModelHarbor-Token": token}
        self.interval, self.last, self.phase = interval, 0.0, None

    def update(self, phase, **fields):
        # Phase changes are always sent; progress within a phase is throttled.
        if phase == self.phase and time.monotonic() - self.last < self.interval:
            return
        self.phase, self.last = phase, time.monotonic()
        try:
            self.session.post(self.url, headers=self.headers, timeout=10,
                              json={"device": self.device, "phase": phase, **fields})
        except requests.RequestException as exc:  # status must never break a sync
            LOG.debug("Status update failed: %s", exc)

class NullReporter:
    def update(self, phase, **fields):
        pass

def acquire_lock(path):
    """Exclusive lock file so a scheduled sync and a dashboard "Scan now" never overlap.
    A lock older than a few hours is treated as left behind by a crashed run."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if time.time() - path.stat().st_mtime > STALE_LOCK_SECONDS:
            path.unlink()
    except FileNotFoundError:
        pass
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        return False

def release_lock(path):
    Path(path).unlink(missing_ok=True)
