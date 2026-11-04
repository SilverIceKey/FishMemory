import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fishmemory.config import load_config
from fishmemory.core import Store
from fishmemory.writing import store_memory
from fishmemory import mcp_server


class WritingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / 'config.json'
        path.write_text(json.dumps({'user_id': 'alice', 'agent_id': 'personal'}), encoding='utf-8')
        self.config = load_config(path)

    def write(self, mode='autonomous'):
        return store_memory(self.config, '茶馆每周三休息', '茶馆', '记一下，我的茶馆每周三休息', mode)

    def test_persist_scope_retry_and_forget(self):
        body = self.write('explicit')
        self.assertTrue(body['persisted'])
        self.assertEqual(body['reply_policy'], 'brief_confirmation')
        ident = body['items'][0]['id']
        self.assertEqual(self.write()['status'], 'duplicate')
        store = Store(self.config.db)
        try:
            self.assertEqual(store.details(('bob', 'personal'), [ident]), [])
            self.assertEqual(store.details(('alice', 'other'), [ident]), [])
            self.assertEqual(store.details(self.config.scope, [ident])[0]['source_verification'], 'caller_supplied')
            store.forget(self.config.scope, ident)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM memory_sources').fetchone()[0], 0)
        finally:
            store.close()
        self.assertEqual(self.write()['status'], 'rejected')

    def test_concurrent_exact_retries(self):
        self.config.ensure_directories()
        Store(self.config.db).close()
        with ThreadPoolExecutor(max_workers=4) as pool:
            bodies = list(pool.map(lambda _: self.write(), range(4)))
        self.assertEqual(sorted(b['status'] for b in bodies), ['duplicate'] * 3 + ['stored'])

    def test_invalid_and_locked_no_success(self):
        self.assertEqual(store_memory(self.config, '', 'topic', 'quote')['status'], 'error')
        self.write()
        lock = sqlite3.connect(self.config.db)
        try:
            lock.execute('BEGIN IMMEDIATE')
            self.assertFalse(self.write()['persisted'])
        finally:
            lock.rollback()
            lock.close()
        self.assertEqual(self.write()['status'], 'duplicate')

    def test_audited_silent_write_without_model(self):
        previous = mcp_server._CONFIG
        mcp_server._CONFIG = self.config
        try:
            body = json.loads(mcp_server.memory_store('偏好中文', '偏好', '我平常喜欢用中文'))
        finally:
            mcp_server._CONFIG = previous
        self.assertEqual(body['reply_policy'], 'silent')
        lines = [json.loads(line) for line in self.config.service['call_log'].read_text(encoding='utf-8').splitlines()]
        self.assertTrue(body['provenance']['audit_logged'])
        self.assertEqual(lines[-1]['tool'], 'memory_store')
        self.assertNotIn('偏好中文', json.dumps(lines, ensure_ascii=False))
