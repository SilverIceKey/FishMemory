import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fishmemory import config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, data=None, name='fishmemory.local.json', text=None):
        path = self.root / name
        path.write_text(text if text is not None else json.dumps(data), encoding='utf-8')
        return path

    def test_defaults_and_relative_paths(self):
        cfg = config.load_config(self.write({}))
        self.assertEqual(cfg.db, (self.root / '.fishmemory/memory.sqlite').resolve())
        self.assertEqual(cfg.scope, ('local', 'dsh'))
        self.assertEqual(cfg.model['base_url'], 'http://127.0.0.1:11435/v1')
        self.assertEqual(cfg.model['model'], 'qwen3.5-0.8b')
        self.assertEqual(cfg.model['api_key_file'], '')
        service = cfg.service
        self.assertEqual(service['host'], '127.0.0.1')
        self.assertEqual(service['port'], 8765)
        self.assertEqual(service['token_file'], (self.root / '.fishmemory/service.token').resolve())
        self.assertEqual(service['call_log'], (self.root / '.fishmemory/mcp-calls.jsonl').resolve())
        self.assertEqual(service['ingest_log'], (self.root / '.fishmemory/ingest.jsonl').resolve())
        self.assertEqual(service['max_body_bytes'], 262144)
        self.assertEqual(service['max_events'], 64)
        self.assertEqual(service['recall_deadline_s'], 20.0)

    def test_nested_model_and_paths_resolve_against_config_dir(self):
        path = self.write({
            'db': 'data/memory.sqlite',
            'user_id': 'alice',
            'agent_id': 'desk',
            'model': {'base_url': 'http://127.0.0.1:9/v1', 'model': 'm',
                      'api_key_file': 'keys/model.key'},
            'service': {'port': 1234, 'token_file': 'run/token',
                        'call_log': 'run/calls.jsonl', 'ingest_log': 'run/ingest.jsonl'},
        })
        cfg = config.load_config(path)
        self.assertEqual(cfg.db, (self.root / 'data/memory.sqlite').resolve())
        self.assertEqual(cfg.scope, ('alice', 'desk'))
        self.assertEqual(cfg.model['api_key_file'], str((self.root / 'keys/model.key').resolve()))
        self.assertEqual(cfg.service['token_file'], (self.root / 'run/token').resolve())
        self.assertEqual(cfg.service['port'], 1234)

    def test_legacy_flat_model_config_is_accepted(self):
        cfg = config.load_config(self.write({
            'base_url': 'http://127.0.0.1:11435/v1',
            'model': 'qwen3.5-0.8b',
            'api_key_file': 'key.txt',
        }))
        self.assertEqual(cfg.model['base_url'], 'http://127.0.0.1:11435/v1')
        self.assertEqual(cfg.model['model'], 'qwen3.5-0.8b')
        self.assertEqual(cfg.model['api_key_file'], str((self.root / 'key.txt').resolve()))

    def test_example_config_keeps_contract_shape(self):
        example = config.REPO_ROOT / config.EXAMPLE_CONFIG_NAME
        self.assertTrue(example.is_file())
        cfg = config.load_config(example)
        self.assertIsInstance(cfg.model, dict)
        self.assertIn('base_url', cfg.model)
        self.assertIn('token_file', cfg.service)
        raw = json.loads(example.read_text(encoding='utf-8-sig'))
        self.assertIsInstance(raw['model'], dict)
        self.assertNotIn('token', raw['service'])
        # The template may point at a key *file*, but never embed key material.
        self.assertNotIn('api_key', raw['model'])

    def test_missing_explicit_config_raises(self):
        with self.assertRaises(config.ConfigError):
            config.resolve_config_path(self.root / 'missing.json')
        with self.assertRaises(config.ConfigError):
            config.resolve_config_path('missing-file.json')

    def test_relative_config_falls_back_to_repo_root(self):
        original = os.getcwd()
        try:
            os.chdir(self.root)
            found = config.resolve_config_path(config.EXAMPLE_CONFIG_NAME)
        finally:
            os.chdir(original)
        self.assertEqual(found, (config.REPO_ROOT / config.EXAMPLE_CONFIG_NAME).resolve())

    def test_invalid_json_raises(self):
        with self.assertRaises(config.ConfigError):
            config.load_config(self.write(text='{not json', name='bad.json'))

    def test_scope_must_not_be_empty(self):
        with self.assertRaises(config.ConfigError):
            config.load_config(self.write({'user_id': ' '}))
        with self.assertRaises(config.ConfigError):
            config.load_config(self.write({'agent_id': ''}))

    def test_host_must_be_loopback(self):
        for host in ('0.0.0.0', '192.168.1.5', '::'):
            with self.assertRaises(config.ConfigError):
                config.load_config(self.write({'service': {'host': host}}))
        cfg = config.load_config(self.write({'service': {'host': 'localhost'}}))
        self.assertEqual(cfg.service['host'], '127.0.0.1')

    def test_invalid_limits_rejected(self):
        cases = [
            {'service': {'port': 'x'}},
            {'service': {'port': 70000}},
            {'service': {'max_events': 0}},
            {'service': {'max_events': True}},
            {'service': {'max_body_bytes': 8}},
            {'service': {'recall_deadline_s': 0}},
            {'model': {'base_url': 'ftp://nope'}},
            {'model': {'model': ''}},
            {'model': {'api_key_file': 5}},
            {'db': ''},
        ]
        for case in cases:
            with self.assertRaises(config.ConfigError, msg=str(case)):
                config.load_config(self.write(case))

    def test_unknown_keys_are_ignored(self):
        cfg = config.load_config(self.write({'future_field': 1, 'service': {'future': True}}))
        self.assertEqual(cfg.service['port'], 8765)

    def test_env_var_and_explicit_resolution(self):
        path = self.write({'user_id': 'env'})
        with mock.patch.dict(os.environ, {config.CONFIG_ENV: str(path)}):
            cfg = config.load_config()
        self.assertEqual(cfg.user_id, 'env')
        self.assertEqual(cfg.source, path.resolve())
        self.assertEqual(config.resolve_config_path(str(path)), path.resolve())

    def test_default_resolution_finds_a_real_file(self):
        found = config.resolve_config_path()
        self.assertTrue(found.is_file())
        self.assertIn(found.name, (config.DEFAULT_CONFIG_NAME, config.EXAMPLE_CONFIG_NAME))

    def test_token_autogenerated_and_reused(self):
        cfg = config.load_config(self.write({'service': {'token_file': 'run/service.token'}}))
        token = cfg.ensure_token()
        self.assertRegex(token, r'^[0-9a-f]{64}$')
        self.assertEqual(token, cfg.ensure_token())
        self.assertEqual(cfg.service['token_file'].read_text(encoding='utf-8'), token)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(cfg.service['token_file'].stat().st_mode), 0o600)

    def test_token_preserved_and_stale_empty_file_replaced(self):
        cfg = config.load_config(self.write({'service': {'token_file': 'token'}}))
        cfg.service['token_file'].write_text('fixed-token', encoding='utf-8')
        self.assertEqual(cfg.ensure_token(), 'fixed-token')
        cfg.service['token_file'].write_text('', encoding='utf-8')
        regenerated = cfg.ensure_token()
        self.assertRegex(regenerated, r'^[0-9a-f]{64}$')

    def test_init_writes_config_token_and_directories(self):
        target = self.root / 'fishmemory.local.json'
        cfg = config.init_config(target)
        self.assertTrue(target.is_file())
        self.assertTrue(cfg.service['token_file'].is_file())
        self.assertTrue(cfg.db.parent.is_dir())
        raw = json.loads(target.read_text(encoding='utf-8-sig'))
        for key in ('db', 'user_id', 'agent_id', 'model', 'service'):
            self.assertIn(key, raw)
        with self.assertRaises(config.ConfigError):
            config.init_config(target)
        config.init_config(target, force=True)

    def test_init_default_target_relative_to_repo_root(self):
        target = config.REPO_ROOT / config.DEFAULT_CONFIG_NAME
        if target.exists():
            self.skipTest('fishmemory.local.json already exists')
        with mock.patch.object(config, 'REPO_ROOT', self.root):
            cfg = config.init_config()
            self.assertEqual(cfg.source, (self.root / config.DEFAULT_CONFIG_NAME).resolve())
            self.assertTrue((self.root / config.DEFAULT_CONFIG_NAME).is_file())


if __name__ == '__main__':
    unittest.main()

