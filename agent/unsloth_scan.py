"""Read cached repository inventory from Unsloth Studio."""
import logging
import os
import re
import time
from pathlib import PurePosixPath
from urllib.parse import urlsplit

LOG = logging.getLogger(__name__)
REPO_ID = re.compile(r'[A-Za-z0-9._-]+/[A-Za-z0-9._-]+')


def api_root(base_url):
    base_url = base_url.strip()
    parsed = urlsplit(base_url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Unsloth URL must be an HTTP(S) endpoint without credentials, query, or fragment')
    return base_url.rstrip('/')


def auth_headers():
    headers = {}
    key = os.environ.get('UNSLOTH_API_KEY', '')
    if key:
        headers['Authorization'] = 'Bearer ' + key
    # API-key callers get no fallback to Unsloth's own Hugging Face token, so gated repos need one sent here.
    hf_token = os.environ.get('UNSLOTH_HF_TOKEN', '')
    if hf_token:
        headers['X-Unsloth-HF-Token'] = hf_token
    return headers


def _valid_repo(repo):
    return isinstance(repo, str) and REPO_ID.fullmatch(repo) is not None


def collect(session, base_url, device):
    base_url = api_root(base_url)
    records = []
    for category in ('cached-gguf', 'cached-models'):
        response = session.get(base_url + '/api/models/' + category, headers=auth_headers(), timeout=30)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get('cached'), list):
            raise ValueError('Unsloth returned an unsupported inventory response')
        for item in body['cached']:
            if not isinstance(item, dict):
                LOG.warning('Skipping an Unsloth %s entry that is not an object', category)
                continue
            repo = item.get('repo_id')
            if not _valid_repo(repo):
                LOG.warning('Skipping an Unsloth %s entry with an unsupported repository identifier', category)
                continue
            if item.get('partial'):
                continue  # still downloading, or an interrupted download: not a usable model yet
            try:
                size = max(0, int(item.get('size_bytes') or 0))
            except (TypeError, ValueError, OverflowError):
                LOG.warning('Skipping Unsloth entry %s with an invalid size', repo)
                continue
            # API-key callers identify a cached copy by its opaque cache_ref; Unsloth accepts it as cache_path.
            reference = item.get('cache_ref') or item.get('cache_path')
            records.append({'device': device, 'path': 'unsloth://' + category + '/' + repo,
                            'name': repo, 'kind': 'unsloth/' + ('gguf' if category == 'cached-gguf' else 'model'),
                            'size_bytes': size, 'modified_ns': 0, 'file_id': None, 'sha256': None,
                            'checkpoint': repo, 'runtime_meta': {'runtime': 'unsloth', 'repo_id': repo,
                                                               'cache_path': reference if isinstance(reference, str) else None}})
    return records


def delete_cached(session, base_url, meta):
    repo, cache_path = meta.get('repo_id'), meta.get('cache_path')
    if not _valid_repo(repo):
        raise ValueError('Invalid Unsloth repository identifier')
    if not isinstance(cache_path, str) or not cache_path or len(cache_path) > 4096:
        raise ValueError('Unsloth cache copy is not identified')
    # The hub route resolves an API key's cache_ref handle back to the cached copy.
    response = session.delete(api_root(base_url) + '/api/hub/delete-cached',
                              headers=auth_headers(), json={'repo_id': repo, 'cache_path': cache_path}, timeout=120)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or data.get('status') != 'deleted':
        raise ValueError('Unsloth did not confirm deletion')


def _downloaded_bytes(session, base, repo, variant, expected_bytes):
    """Bytes on disk so far, or None when Unsloth cannot say (progress is best effort)."""
    try:
        response = session.get(base + '/api/hub/gguf-download-progress', headers=auth_headers(),
                               params={'repo_id': repo, 'variant': variant, 'expected_bytes': expected_bytes or 0}, timeout=30)
        response.raise_for_status()
        data = response.json()
        done = int(data.get('downloaded_bytes') or 0) if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001 - a failed progress read must not fail the download
        LOG.debug('Unsloth progress unavailable: %s', type(exc).__name__)
        return None
    return max(0, done) if done is not None else None


def download(session, base_url, repo, remote_path, report_progress, generation=None, expected_bytes=None, sleep=time.sleep):
    if not _valid_repo(repo):
        raise ValueError('Invalid Unsloth repository identifier')
    if not isinstance(remote_path, str) or not remote_path.lower().endswith('.gguf'):
        raise ValueError('Unsloth download requires a GGUF file')
    base = api_root(base_url)
    response = session.get(base + '/api/models/gguf-variants', headers=auth_headers(),
                           params={'repo_id': repo}, timeout=60)
    response.raise_for_status()
    data = response.json()
    variants = data.get('variants') if isinstance(data, dict) else None
    if not isinstance(variants, list):
        raise ValueError('Unsloth returned an unsupported variant list')
    matches = [v for v in variants if isinstance(v, dict) and v.get('filename') == remote_path]
    if not matches:
        matches = [v for v in variants if isinstance(v, dict) and v.get('filename') == PurePosixPath(remote_path).name]
    if len(matches) != 1 or not isinstance(matches[0].get('quant'), str) or not matches[0]['quant']:
        raise ValueError('Unsloth cannot uniquely identify this GGUF variant')
    variant = matches[0]['quant']
    if sum(isinstance(v, dict) and v.get('quant') == variant for v in variants) != 1:
        raise ValueError('Unsloth cannot uniquely identify this GGUF variant')
    if generation is None:
        response = session.post(base + '/api/hub/download', headers=auth_headers(),
                                json={'repo_id': repo, 'gguf_variant': variant, 'transport_mode': 'auto'}, timeout=60)
        response.raise_for_status()
        status = response.json()
        if not isinstance(status, dict) or (status.get('accepted') is False and status.get('attached') is not True):
            raise ValueError('Unsloth refused to start the download')
        generation = status.get('generation')
        if not isinstance(generation, int) or generation < 1:
            raise ValueError('Unsloth did not return a download generation')
        report_progress(0, str(generation))
    else:
        generation = int(generation)
    last_done = 0
    while True:
        response = session.get(base + '/api/hub/download-status', headers=auth_headers(),
                               params={'repo_id': repo, 'gguf_variant': variant}, timeout=60)
        response.raise_for_status()
        status = response.json()
        if not isinstance(status, dict) or status.get('generation') not in (generation,):
            raise ValueError('Unsloth download was replaced or interrupted')
        state = status.get('state')
        if state == 'complete':
            if expected_bytes:
                report_progress(int(expected_bytes), str(generation))
            return
        if state != 'running':
            reason = status.get('error')
            detail = reason.strip()[:200] if isinstance(reason, str) and reason.strip() else 'state ' + str(state)
            raise ValueError('Unsloth download stopped: ' + detail)
        done = _downloaded_bytes(session, base, repo, variant, expected_bytes)
        if done is not None:
            last_done = max(last_done, done)
        report_progress(last_done, str(generation))
        sleep(5)
