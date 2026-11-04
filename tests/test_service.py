import json
import socket
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib import error as urlerror
from urllib import request as urlrequest

from fishmemory import config as config_module
from fishmemory import service
from fishmemory.core import Store


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class FakeModel:
    """Deterministic injected local model; tests never need llama.cpp."""

    def __init__(self, config):
        self.config = config
        self.calls = []

    def plan(self, query, topics, previous, deadline):
        self.calls.append({'elapsed_ms': 0.5,
                           'usage': {'prompt_tokens': 7, 'completion_tokens': 2}})
        return {'topic': '', 'keywords': ['SQLite'], 'historical': True}

    def select(self, query, candidates, deadline):
        self.calls.append({'elapsed_ms': 0.5,
                           'usage': {'prompt_tokens': 9, 'completion_tokens': 1}})
        return [candidate['id'] for candidate in candidates][:1]


class ServiceTests(unittest.TestCase):
    def test_selected_store_auth_scope_and_audit(self):
        payload = {'content': '喜欢中文', 'topic': '偏好', 'source_quote': '我喜欢中文'}
        self.assertEqual(self.request('POST', '/v1/store', payload, token='')[0], 401)
        self.assertEqual(self.request('POST', '/v1/store', dict(payload, user_id='other'))[0], 400)
        code, body = self.request('POST', '/v1/store', payload)
        self.assertEqual(code, 200)
        self.assertTrue(body['persisted'])
        self.assertEqual(body['reply_policy'], 'silent')
        self.assertEqual(self.stored_ids(), {body['items'][0]['id']})
        self.assertEqual(self.stored_ids(('other', 'a')), set())
        log = json.loads(self.call_log.read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual((log['source'], log['tool']), ('http', 'memory_store'))

    def test_incomplete_body_times_out_and_server_remains_available(self):
        with mock.patch.object(service, 'REQUEST_TIMEOUT_S', 0.1):
            with socket.create_connection(('127.0.0.1', self.port), timeout=2) as sock:
                request = ('POST /v1/events HTTP/1.1\r\nHost: localhost\r\n'
                           f'Authorization: Bearer {self.token}\r\n'
                           'Content-Length: 100\r\n\r\n{')
                sock.sendall(request.encode())
                response = b''
                while chunk := sock.recv(4096):
                    response += chunk
                self.assertIn(b'408', response)
                self.assertIn(b'request_timeout', response)
        self.assertEqual(self.request('GET', '/healthz')[0], 200)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'memory.sqlite'
        self.token_file = self.root / 'service.token'
        self.call_log = self.root / 'calls.jsonl'
        self.ingest_log = self.root / 'ingest.jsonl'
        self.key_file = self.root / 'model.key'
        self.key_file.write_text('dummy-test-key', encoding='utf-8')
        self.model_port = _free_port()
        self.config_path = self.root / 'fishmemory.local.json'
        self.config_path.write_text(json.dumps({
            'db': str(self.db),
            'user_id': 'u',
            'agent_id': 'a',
            'model': {
                'base_url': f'http://127.0.0.1:{self.model_port}/v1',
                'model': 'test-model',
                'api_key_file': str(self.key_file),
            },
            'service': {
                'host': '127.0.0.1',
                'port': 0,
                'token_file': str(self.token_file),
                'call_log': str(self.call_log),
                'ingest_log': str(self.ingest_log),
                'max_body_bytes': 4096,
                'max_events': 3,
                'recall_deadline_s': 2,
            },
        }), encoding='utf-8')
        self.cfg = config_module.load_config(self.config_path)
        self.server = service.create_server(self.cfg)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.token = self.token_file.read_text(encoding='utf-8').strip()

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    # -- helpers ---------------------------------------------------------
    def request(self, method, path, payload=None, token='default', raw=None):
        data = raw if raw is not None else (
            None if payload is None else json.dumps(payload).encode('utf-8'))
        request = urlrequest.Request(
            f'http://127.0.0.1:{self.port}{path}', data=data, method=method)
        if data is not None:
            request.add_header('Content-Type', 'application/json')
        if token == 'default':
            token = self.token
        if token:
            request.add_header('Authorization', 'Bearer ' + token)
        try:
            with urlrequest.urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read().decode('utf-8'))
        except urlerror.HTTPError as error:
            return error.code, json.loads(error.read().decode('utf-8'))

    def event(self, event_id, text='用户喜欢 SQLite 持久化', state=None):
        item = {'id': event_id, 'session': 'sess-1', 'topic': '偏好',
                'text': text, 'time': '2026-10-30T00:00:00Z'}
        if state:
            item['state'] = state
        return item

    def stored_ids(self, scope=('u', 'a')):
        store = Store(self.db)
        try:
            rows = store.db.execute(
                'SELECT event_id FROM events WHERE user_id=? AND agent_id=?',
                scope).fetchall()
            return {row[0] for row in rows}
        finally:
            store.close()

    # -- tests -----------------------------------------------------------
    def test_healthz_is_unauthenticated_and_never_echoes_token(self):
        status, body = self.request('GET', '/healthz', token=None)
        self.assertEqual(status, 200)
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['version'], 1)
        self.assertEqual(body['db'], str(self.cfg.db))
        self.assertNotIn(self.token, json.dumps(body))

    def test_auth_unknown_paths_and_bad_methods(self):
        status, body = self.request('POST', '/v1/events', {'events': []}, token=None)
        self.assertEqual(status, 401)
        self.assertEqual(body, {'status': 'error', 'error': 'unauthorized'})
        status, _ = self.request('POST', '/v1/events', {'events': []}, token='wrong')
        self.assertEqual(status, 401)
        status, body = self.request('GET', '/nope')
        self.assertEqual((status, body['error']), (404, 'not_found'))
        status, body = self.request('POST', '/nope', {})
        self.assertEqual((status, body['error']), (404, 'not_found'))
        status, body = self.request('GET', '/v1/events')
        self.assertEqual((status, body['error']), (405, 'method_not_allowed'))

    def test_events_idempotency_and_conflict_are_per_item(self):
        status, body = self.request('POST', '/v1/events', {'events': [self.event('e1')]})
        self.assertEqual(status, 200)
        self.assertEqual(body['results'], [{'id': 'e1', 'status': 'stored'}])
        status, body = self.request('POST', '/v1/events', {'events': [self.event('e1')]})
        self.assertEqual(body['results'], [{'id': 'e1', 'status': 'duplicate'}])
        status, body = self.request(
            'POST', '/v1/events', {'events': [self.event('e1', '不同的文本')]})
        self.assertEqual(body['results'], [{'id': 'e1', 'status': 'error',
                                            'error': 'conflicting_event_id'}])
        status, body = self.request(
            'POST', '/v1/events', {'events': [self.event('e1'), self.event('e2')]})
        self.assertEqual([entry['status'] for entry in body['results']],
                         ['duplicate', 'stored'])
        self.assertEqual(self.stored_ids(), {'e1', 'e2'})

    def test_events_validate_per_item_without_aborting_batch(self):
        bad_text = {'id': 'bad', 'session': 's', 'topic': 't',
                    'time': '2026-10-30T00:00:00Z'}
        bad_state = self.event('state', state='unknown')
        status, body = self.request('POST', '/v1/events', {'events': [
            self.event('ok1'), bad_text, bad_state]})
        self.assertEqual(status, 200)
        self.assertEqual([entry['status'] for entry in body['results']],
                         ['stored', 'error', 'error'])
        self.assertEqual(body['results'][1]['error'], 'invalid_event')
        self.assertEqual(body['results'][2]['error'], 'invalid_state')
        status, body = self.request('POST', '/v1/events', {'events': [self.event('ok2')]})
        self.assertEqual(body['results'], [{'id': 'ok2', 'status': 'stored'}])
        self.assertEqual(self.stored_ids(), {'ok1', 'ok2'})

    def test_events_scope_comes_from_config_only(self):
        item = dict(self.event('e1'), user_id='attacker', agent_id='attacker')
        status, body = self.request('POST', '/v1/events', {'events': [item]})
        self.assertEqual(body['results'], [{'id': 'e1', 'status': 'stored'}])
        self.assertEqual(self.stored_ids(('u', 'a')), {'e1'})
        self.assertEqual(self.stored_ids(('attacker', 'a')), set())
        self.assertEqual(self.stored_ids(('u', 'attacker')), set())
        self.assertEqual(self.stored_ids(('attacker', 'attacker')), set())

    def test_historical_state_is_preserved(self):
        status, body = self.request(
            'POST', '/v1/events', {'events': [self.event('old', '用户以前喜欢 Java',
                                                        state='historical')]})
        self.assertEqual(body['results'], [{'id': 'old', 'status': 'stored'}])
        store = Store(self.db)
        try:
            self.assertEqual(store.search(('u', 'a'), 'Java', historical=False), [])
            self.assertEqual(store.search(('u', 'a'), 'Java')[0]['id'], 'old')
        finally:
            store.close()

    def test_event_and_body_limits(self):
        status, body = self.request('POST', '/v1/events', {
            'events': [self.event(f'e{i}') for i in range(4)]})
        self.assertEqual(status, 413)
        self.assertEqual(body['error'], 'too_many_events')
        self.assertFalse(self.db.exists())
        status, body = self.request('POST', '/v1/events', {
            'events': [self.event('big', 'x' * 5000)]})
        self.assertEqual(status, 413)
        self.assertEqual(body['error'], 'payload_too_large')

    def test_malformed_bodies_use_unified_json_errors(self):
        status, body = self.request('POST', '/v1/events', raw=b'{not json')
        self.assertEqual((status, body['error']), (400, 'invalid_json'))
        status, body = self.request('POST', '/v1/events', token='default')
        self.assertEqual(status, 400)
        self.assertIn(body['error'], ('invalid_request', 'invalid_json'))
        status, body = self.request('POST', '/v1/events', {'not_events': []})
        self.assertEqual((status, body['error']), (400, 'invalid_request'))

    def test_forget_tombstone_and_replay(self):
        self.request('POST', '/v1/events', {'events': [self.event('e1')]})
        status, body = self.request('POST', '/v1/forget', {'id': 'e1'})
        self.assertEqual((status, body), (200, {'status': 'forgotten'}))
        store = Store(self.db)
        try:
            self.assertEqual(store.search(('u', 'a'), 'SQLite'), [])
        finally:
            store.close()
        status, body = self.request(
            'POST', '/v1/events', {'events': [self.event('e1')]})
        self.assertEqual(body['results'], [{'id': 'e1', 'status': 'rejected'}])
        status, body = self.request('POST', '/v1/forget', {'id': 'e1'})
        self.assertEqual(body, {'status': 'absent'})

    def test_forget_absent_id_still_blocks_replay(self):
        status, body = self.request('POST', '/v1/forget', {'id': 'never-seen'})
        self.assertEqual((status, body), (200, {'status': 'absent'}))
        status, body = self.request(
            'POST', '/v1/events', {'events': [self.event('never-seen')]})
        self.assertEqual(body['results'], [{'id': 'never-seen', 'status': 'rejected'}])

    def test_recall_with_model_unavailable_is_structured(self):
        self.request('POST', '/v1/events', {'events': [self.event('e1')]})
        status, body = self.request('POST', '/v1/recall', {'query': 'SQLite'})
        self.assertEqual(status, 200)
        self.assertIn(body['status'], ('partial', 'error'))
        self.assertEqual(body['items'], [])
        self.assertTrue(body['uncertainty'])
        self.assertIn('error', body)
        self.assertTrue(body.get('fallback_candidates'))
        self.assertTrue(all('id' in candidate for candidate in body['fallback_candidates']))
        # Failure must not be written as a memory and must not leak secrets.
        log_text = self.call_log.read_text(encoding='utf-8')
        self.assertNotIn(self.token, log_text)
        self.assertNotIn('dummy-test-key', log_text)
        self.assertEqual(self.stored_ids(), {'e1'})

    def test_recall_found_with_injected_model_writes_call_log(self):
        self.request('POST', '/v1/events', {'events': [self.event('e1')]})
        with mock.patch.object(service, 'LocalModel', FakeModel):
            status, body = self.request('POST', '/v1/recall', {'query': 'SQLite'})
        self.assertEqual(status, 200)
        self.assertEqual(body['status'], 'found')
        self.assertEqual([item['id'] for item in body['items']], ['e1'])
        self.assertTrue(body['uncertainty'])
        self.assertNotIn('error', body)
        lines = [json.loads(line) for line in
                 self.call_log.read_text(encoding='utf-8').splitlines()]
        entry = lines[-1]
        self.assertEqual(entry['source'], 'http')
        self.assertEqual(entry['status'], 'found')
        self.assertEqual(entry['ids'], ['e1'])
        self.assertTrue(entry['calls'])
        self.assertEqual((entry['user_id'], entry['agent_id']), ('u', 'a'))
        self.assertNotIn(self.token, json.dumps(entry))
        self.assertNotIn('dummy-test-key', json.dumps(entry))

    def test_recall_scope_in_body_is_ignored(self):
        self.request('POST', '/v1/events', {'events': [self.event('e1')]})
        with mock.patch.object(service, 'LocalModel', FakeModel):
            status, body = self.request('POST', '/v1/recall', {
                'query': 'SQLite', 'user_id': 'attacker', 'agent_id': 'attacker'})
        self.assertEqual(body['status'], 'found')
        self.assertEqual([item['id'] for item in body['items']], ['e1'])

    def test_recall_rejects_invalid_arguments(self):
        status, body = self.request('POST', '/v1/recall', {'query': ''})
        self.assertEqual((status, body['error']), (400, 'invalid_query'))
        status, body = self.request('POST', '/v1/recall', {'query': 'x' * 2001})
        self.assertEqual((status, body['error']), (400, 'invalid_query'))
        status, body = self.request(
            'POST', '/v1/recall', {'query': 'x', 'max_rounds': 0})
        self.assertEqual((status, body['error']), (400, 'invalid_request'))
        status, body = self.request(
            'POST', '/v1/recall', {'query': 'x', 'max_rounds': True})
        self.assertEqual((status, body['error']), (400, 'invalid_request'))

    def test_recall_without_database_returns_503(self):
        self.assertFalse(self.db.exists())
        status, body = self.request('POST', '/v1/recall', {'query': 'SQLite'})
        self.assertEqual(status, 503)
        self.assertEqual(body, {'status': 'error', 'error': 'database_unavailable'})
        self.assertFalse(self.db.exists())

    def test_busy_database_returns_503_and_recovers(self):
        self.request('POST', '/v1/events', {'events': [self.event('warmup')]})
        blocker = sqlite3.connect(str(self.db), timeout=1, isolation_level=None)
        try:
            blocker.execute('BEGIN IMMEDIATE')
            status, body = self.request(
                'POST', '/v1/events', {'events': [self.event('locked')]})
        finally:
            try:
                blocker.execute('ROLLBACK')
            except sqlite3.Error:
                pass
            blocker.close()
        self.assertEqual(status, 503)
        self.assertEqual(body, {'status': 'error', 'error': 'database_busy'})
        status, body = self.request(
            'POST', '/v1/events', {'events': [self.event('after')]})
        self.assertEqual(status, 200)
        self.assertEqual(body['results'], [{'id': 'after', 'status': 'stored'}])

    def test_ingest_log_is_local_jsonl_without_secrets(self):
        self.request('POST', '/v1/events', {'events': [self.event('e1')]})
        lines = [json.loads(line) for line in
                 self.ingest_log.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(lines), 1)
        self.assertEqual((lines[0]['id'], lines[0]['status']), ('e1', 'stored'))
        self.assertNotIn(self.token, json.dumps(lines))
        self.assertNotIn('dummy-test-key', json.dumps(lines))
        self.assertNotIn('用户喜欢', json.dumps(lines))  # text is not logged

    def test_token_file_is_generated_with_expected_shape(self):
        self.assertRegex(self.token, r'^[0-9a-f]{64}$')
        status, body = self.request('GET', '/healthz', token=None)
        self.assertEqual(status, 200)


if __name__ == '__main__':
    unittest.main()
