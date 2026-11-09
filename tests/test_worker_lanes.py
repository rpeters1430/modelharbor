"""The agent's dashboard worker runs each kind of job on its own thread."""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
import modelharbor_agent as agent


def test_lanes_split_long_jobs_from_quick_ones(monkeypatch):
    monkeypatch.setattr(agent, "discover_roots", lambda: [])
    lanes = agent.worker_lanes({"server": "http://nas", "device": "pc"}, "t")
    labels = {name: [label for label, _ in steps] for name, steps in lanes.items()}
    assert labels == {"scan": ["Scan request"], "control": ["Delete", "Cleanup"], "hashes": ["Hash job"],
                      "downloads": ["Download"], "converts": ["Conversion"]}
    off = agent.worker_lanes({"server": "http://nas", "device": "pc", "downloads_enabled": False,
                              "converts_enabled": False, "deletes_enabled": False}, "t")
    assert {name: [label for label, _ in steps] for name, steps in off.items()} == {
        "scan": ["Scan request"], "hashes": ["Hash job"]}


def test_lane_drains_its_queue_and_survives_errors():
    stop, calls = threading.Event(), []
    jobs = [True, True, False]

    def step(session):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("NAS hiccup")
        if len(calls) >= 4:
            stop.set()
        return jobs.pop(0) if jobs else False

    started = time.monotonic()
    agent._lane("test", 0.05, [("Step", step)], stop)
    assert len(calls) == 4 and time.monotonic() - started < 2


def test_a_long_download_does_not_block_hash_jobs(monkeypatch):
    stop, hashed, release = threading.Event(), threading.Event(), threading.Event()

    def download(session):
        release.wait(5)  # a multi-hour download
        return False

    def hash_job(session):
        hashed.set()
        return False

    monkeypatch.setattr(agent, "worker_lanes", lambda cfg, token: {
        "downloads": [("Download", download)], "hashes": [("Hash job", hash_job)]})
    worker = threading.Thread(target=agent.run_hash_worker, args=({"hash_poll_seconds": 10}, "t", stop))
    worker.start()
    try:
        assert hashed.wait(2), "hash job waited on the download"
    finally:
        stop.set()
        release.set()
        worker.join(5)
    assert not worker.is_alive()
