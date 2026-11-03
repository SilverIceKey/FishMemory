import asyncio
import json
import socket
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fishmemory import config as config_module
from fishmemory.core import Store

try:
    from fishmemory import mcp_server
except ImportError:  # pragma: no cover - mcp is installed in .venv
    mcp_server = None


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class FakeModel:
    def __init__(self, config):
        self.config = config
        self.calls = []

    def plan(self, query, topics, previous, deadline):
        self.calls.append({'elapsed_ms': 0.5,
                           'usage': {'prompt_tokens': 5, 'completion_tokens': 1}})
        return {'topic': '', 'keywords': ['SQLite'], 'historical': True}

    def select(self, query, candidates, deadline):
        self.calls.append({'elapsed_ms': 0.5,
                           'usage': {'prompt_tokens': 6, 'completion_tokens': 1}})
        return [candidate['id'] for candidate in candidates][:1]


@unittest.skipIf(mcp_server is None, 'mcp package not installed')
class McpServerTests(unittest.TestCase):
    def test_database_lock_returns_error_then_recovers(self):
        self.store_event(self.event())
        lock = sqlite3.connect(self.db)
        try:
            lock.execute('BEGIN EXCLUSIVE')
            body = json.loads(mcp_server.memory_recall('SQLite'))
            self.assertEqual(body['error'], 'database_busy')
            self.assertEqual(body['items'], [])
        finally:
            lock.rollback()
            lock.close()
        with mock.patch.object(mcp_server, 'LocalModel', FakeModel):
            body = json.loads(mcp_server.memory_recall('SQLite'))
        self.assertEqual(body['status'], 'found')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'memory.sqlite'
        self.call_log = self.root / 'calls.jsonl'
        self.key_file = self.root / 'model.key'
        self.key_file.write_text('dummy-test-key', encoding='utf-8')
        self.config_path = self.root / 'fishmemory.local.json'
        self.config_path.write_text(json.dumps({
            'db': str(self.db),
            'user_id': 'u',
            'agent_id': 'a',
            'model': {
                'base_url': f'http://127.0.0.1:{_free_port()}/v1',
                'model': 'test-model',
                'api_key_file': str(self.key_file),
            },
            'service': {'call_log': str(self.call_log), 'recall_deadline_s': 2},
        }), encoding='utf-8')
        mcp_server._CONFIG = None
        mcp_server._load(str(self.config_path))
        self.addCleanup(self._reset)

    def _reset(self):
        mcp_server._CONFIG = None

    def event(self, event_id='e1'):
        return {'id': event_id, 'session': 's', 'topic': '偏好',
                'text': '用户喜欢 SQLite 持久化', 'time': '2026-10-30T00:00:00Z'}

    def store_event(self, event):
        store = Store(self.db)
        try:
            self.assertEqual(store.ingest(('u', 'a'), event), 'stored')
        finally:
            store.close()

    def test_single_tool_with_no_scope_arguments(self):
        tools = asyncio.run(mcp_server.server.list_tools())
        self.assertEqual([tool.name for tool in tools], ['memory_recall'])
        schema = tools[0].inputSchema
        self.assertEqual(set(schema.get('properties', {})), {'query'})
        self.assertEqual(schema.get('required'), ['query'])

    def test_invalid_query_is_structured_error(self):
        for query in ('', 'x' * 2001):
            body = json.loads(mcp_server.memory_recall(query))
            self.assertEqual(body['status'], 'error')
            self.assertEqual(body['items'], [])
            self.assertEqual(body['error'], 'invalid_query')
            self.assertTrue(body['uncertainty'])

    def test_missing_database_is_error_and_not_created(self):
        body = json.loads(mcp_server.memory_recall('SQLite'))
        self.assertEqual(body['status'], 'error')
        self.assertEqual(body['items'], [])
        self.assertEqual(body['error'], 'database_unavailable')
        self.assertFalse(self.db.exists())

    def test_model_unavailable_returns_partial_or_error_without_fabrication(self):
        self.store_event(self.event())
        body = json.loads(mcp_server.memory_recall('SQLite'))
        self.assertIn(body['status'], ('partial', 'error'))
        self.assertEqual(body['items'], [])
        self.assertIn('error', body)
        self.assertTrue(body['uncertainty'])
        self.assertTrue(body.get('fallback_candidates'))
        entry = json.loads(self.call_log.read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual(entry['source'], 'mcp')
        self.assertIn(entry['status'], ('partial', 'error'))
        self.assertNotIn('dummy-test-key', json.dumps(entry))

    def test_found_result_with_injected_model(self):
        self.store_event(self.event())
        with mock.patch.object(mcp_server, 'LocalModel', FakeModel):
            body = json.loads(mcp_server.memory_recall('SQLite'))
        self.assertEqual(body['status'], 'found')
        self.assertEqual([item['id'] for item in body['items']], ['e1'])
        self.assertNotIn('error', body)
        entry = json.loads(self.call_log.read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual((entry['source'], entry['status']), ('mcp', 'found'))
        self.assertEqual((entry['user_id'], entry['agent_id']), ('u', 'a'))
        self.assertTrue(entry['calls'])

    def test_config_failure_is_structured_error(self):
        mcp_server._CONFIG = None
        with mock.patch.object(mcp_server.config_module, 'load_config',
                               side_effect=config_module.ConfigError('missing')):
            body = json.loads(mcp_server.memory_recall('SQLite'))
        self.assertEqual(body['status'], 'error')
        self.assertEqual(body['error'], 'config_unavailable')
        self.assertEqual(body['items'], [])


if __name__ == '__main__':
    unittest.main()

