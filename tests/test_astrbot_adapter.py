import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

from fishmemory import astrbot_adapter as adapter, model_endpoint as backend
from fishmemory.core import Store


class Event:
    def __init__(self, sender='alice', bot='bot', platform='qq', kind='FriendMessage', group=''):
        self.group = group
        self.sender, self.bot, self.platform, self.kind = sender, bot, platform, kind
    def get_sender_id(self): return self.sender
    def get_platform_id(self): return self.platform
    def get_self_id(self): return self.bot
    def get_group_id(self): return self.group
    def get_message_type(self): return SimpleNamespace(value=self.kind)


class AstrBotTests(unittest.TestCase):
    def test_private_identity_and_restarts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cfg = adapter.scope_config(root, Event())
            saved = json.loads(adapter.store(cfg, '喜欢中文', '偏好', '我喜欢中文', 'autonomous'))
            db = Store(cfg.db)
            try:
                ident = saved['items'][0]['id']
                for event in [Event('bob'), Event(bot='other'), Event(platform='else')]:
                    self.assertFalse(db.details(adapter.scope_config(root, event).scope, [ident]))
                self.assertTrue(db.details(adapter.scope_config(root, Event()).scope, [ident]))
                group = adapter.scope_config(root, Event(kind='GroupMessage', group='A'))
                self.assertTrue(db.details(group.scope, [ident]))
                group_id = json.loads(adapter.store(group, '群约定', '主题', '群约定', 'explicit'))['items'][0]['id']
                self.assertTrue(db.details(group.scope, [group_id]))
                for same in [Event(), Event(kind='GroupMessage', group='B')]:
                    self.assertTrue(db.details(adapter.scope_config(root, same).scope, [group_id]))
                for other in [Event('bob'), Event('bob', kind='GroupMessage', group='A')]:
                    self.assertFalse(db.details(adapter.scope_config(root, other).scope, [group_id]))
                for event in [Event(''), Event(kind='GroupMessage'), Event(kind='OtherMessage')]:
                    with self.assertRaises(ValueError): adapter.scope_config(root, event)
            finally:
                db.close()

    def test_discovery_uses_bearer_and_refuses_redirect(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.headers.get('Authorization') != 'Bearer synthetic':
                    self.send_error(401)
                    return
                if self.path != '/v1/models':
                    self.send_response(302)
                    self.send_header('Location', '/v1/models')
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"data":[{"id":"small"},{"id":"large"}]}')
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = 'http://127.0.0.1:' + str(server.server_port)
            cfg = adapter.model_settings({'baseUrl': base, 'apiKey': 'synthetic'})
            ids = backend.models(cfg)
            self.assertEqual(backend.choose(ids, 'small'), 'small')
            self.assertEqual(backend.choose(['small'], ''), 'small')
            with self.assertRaises(ValueError): backend.choose(ids, '')
            with self.assertRaises(HTTPError): backend.models(dict(cfg, api_key='wrong'))
            with self.assertRaises(HTTPError): backend.models(dict(cfg, base_url=base + '/redirect'))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
