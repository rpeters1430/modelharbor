import pytest

from agent import unsloth_scan


class Response:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class Session:
    def get(self, url, **kwargs):
        if url.endswith('/cached-gguf'):
            return Response({'cached': [{'repo_id': 'owner/model-GGUF', 'size_bytes': 42}]})
        return Response({'cached': [{'repo_id': 'owner/other', 'size_bytes': 10}]})


def test_collects_cached_gguf_and_other_models():
    records = unsloth_scan.collect(Session(), 'http://localhost:8888', 'pc:unsloth')
    assert {r['checkpoint'] for r in records} == {'owner/model-GGUF', 'owner/other'}
    assert all(r['device'] == 'pc:unsloth' for r in records)


def test_rejects_url_with_credentials():
    with pytest.raises(ValueError):
        unsloth_scan.collect(Session(), 'http://user:secret@localhost:8888', 'pc:unsloth')


def test_preserves_opaque_cache_reference_for_deletion():
    class CacheSession:
        def get(self, url, **kwargs):
            return Response({'cached': [{'repo_id': 'owner/model', 'size_bytes': 42, 'cache_path': 'ref:abc'}]} if url.endswith('/cached-gguf') else {'cached': []})
    [record] = unsloth_scan.collect(CacheSession(), 'http://localhost:8888', 'pc:unsloth')
    assert record['runtime_meta']['cache_path'] == 'ref:abc'


def test_delete_passes_the_identified_copy_to_unsloth():
    class DeleteSession:
        def delete(self, url, **kwargs):
            assert url == 'http://localhost:8888/api/hub/delete-cached'
            assert kwargs['json'] == {'repo_id': 'owner/model', 'cache_path': 'ref:abc'}
            return Response({'status': 'deleted'})
    unsloth_scan.delete_cached(DeleteSession(), 'http://localhost:8888', {'repo_id': 'owner/model', 'cache_path': 'ref:abc'})


def test_download_resolves_the_exact_gguf_variant_and_waits_for_completion():
    class DownloadSession:
        def __init__(self):
            self.posts = []
        def get(self, url, **kwargs):
            if url.endswith('/gguf-variants'):
                return Response({'variants': [{'filename': 'model-Q4_K_M.gguf', 'quant': 'Q4_K_M'}]})
            return Response({'state': 'complete', 'generation': 2})
        def post(self, url, **kwargs):
            self.posts.append(kwargs['json'])
            return Response({'accepted': True, 'state': 'running', 'generation': 2})
    session = DownloadSession()
    unsloth_scan.download(session, 'http://localhost:8888', 'owner/model', 'model-Q4_K_M.gguf', lambda *args: None)
    assert session.posts == [{'repo_id': 'owner/model', 'gguf_variant': 'Q4_K_M', 'transport_mode': 'auto'}]


def test_download_refuses_ambiguous_variants():
    class Ambiguous:
        def get(self, url, **kwargs):
            return Response({'variants': [{'filename': 'model.gguf', 'quant': 'Q4'}, {'filename': 'model.gguf', 'quant': 'Q5'}]})
    with pytest.raises(ValueError, match='uniquely'):
        unsloth_scan.download(Ambiguous(), 'http://localhost:8888', 'owner/model', 'model.gguf', lambda *args: None)


def test_delete_requires_an_explicit_success_response():
    class DeleteSession:
        def delete(self, url, **kwargs):
            return Response({'status': 'skipped'})
    with pytest.raises(ValueError, match='confirm'):
        unsloth_scan.delete_cached(DeleteSession(), 'http://localhost:8888', {'repo_id': 'owner/model', 'cache_path': 'ref:abc'})


def test_inventory_prefers_cache_ref_and_skips_partial_and_invalid_rows():
    class Rows:
        def get(self, url, **kwargs):
            if url.endswith('/cached-gguf'):
                return Response({'cached': [
                    {'repo_id': 'owner/good', 'size_bytes': 5, 'cache_path': '/home/x/hub/models--owner--good', 'cache_ref': 'ref:1'},
                    {'repo_id': 'owner/half', 'size_bytes': 5, 'partial': True},
                    {'repo_id': 'local model', 'size_bytes': 5},
                    {'repo_id': 'owner/badsize', 'size_bytes': 'lots'},
                    'not-a-row']})
            return Response({'cached': [{'repo_id': 'owner/other', 'size_bytes': 1}]})
    records = unsloth_scan.collect(Rows(), 'http://localhost:8888', 'pc:unsloth')
    assert [r['name'] for r in records] == ['owner/good', 'owner/other']
    assert records[0]['runtime_meta']['cache_path'] == 'ref:1'
    assert records[1]['runtime_meta']['cache_path'] is None


def test_hf_token_is_sent_for_gated_repositories(monkeypatch):
    monkeypatch.setenv('UNSLOTH_API_KEY', 'sk-unsloth-x')
    monkeypatch.setenv('UNSLOTH_HF_TOKEN', 'hf_x')
    assert unsloth_scan.auth_headers() == {'Authorization': 'Bearer sk-unsloth-x', 'X-Unsloth-HF-Token': 'hf_x'}
    monkeypatch.delenv('UNSLOTH_HF_TOKEN')
    assert 'X-Unsloth-HF-Token' not in unsloth_scan.auth_headers()


class RunningThen:
    """Variant list, then download-status answers in turn, with byte progress in between."""
    def __init__(self, states, progress=None):
        self.states, self.progress = list(states), progress
    def get(self, url, **kwargs):
        if url.endswith('/gguf-variants'):
            return Response({'variants': [{'filename': 'm-Q4_K_M.gguf', 'quant': 'Q4_K_M'}]})
        if url.endswith('/gguf-download-progress'):
            if self.progress is None:
                raise OSError('no progress route')
            return Response({'downloaded_bytes': self.progress})
        return Response(self.states.pop(0))


def test_download_reports_bytes_and_survives_missing_progress_route():
    reported = []
    session = RunningThen([{'state': 'running', 'generation': 3}, {'state': 'complete', 'generation': 3}], progress=40)
    unsloth_scan.download(session, 'http://localhost:8888', 'owner/m', 'm-Q4_K_M.gguf',
                          lambda done, ref: reported.append((done, ref)), generation='3', expected_bytes=100, sleep=lambda s: None)
    assert reported == [(40, '3'), (100, '3')]
    reported.clear()
    session = RunningThen([{'state': 'running', 'generation': 3}, {'state': 'complete', 'generation': 3}])
    unsloth_scan.download(session, 'http://localhost:8888', 'owner/m', 'm-Q4_K_M.gguf',
                          lambda done, ref: reported.append((done, ref)), generation='3', sleep=lambda s: None)
    assert reported == [(0, '3')]


def test_download_failure_carries_unsloths_reason():
    session = RunningThen([{'state': 'error', 'generation': 3, 'error': 'Repository is gated'}])
    with pytest.raises(ValueError, match='Repository is gated'):
        unsloth_scan.download(session, 'http://localhost:8888', 'owner/m', 'm-Q4_K_M.gguf', lambda *a: None, generation='3')
