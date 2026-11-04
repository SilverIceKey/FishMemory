import json
import tomllib
import unittest
from fishmemory.integration import HOSTS, render


class IntegrationTests(unittest.TestCase):
    def test_host_snippets_preserve_paths(self):
        python = 'C:\\Program Files\\工具\\python.exe'
        config = 'D:\\记忆 数据\\config.json'
        for host in HOSTS:
            name, text = render(host, python, config)
            data = tomllib.loads(text) if name.endswith('.toml') else json.loads(text)
            if host == 'codex' or host == 'hermes':
                entry = data['mcp_servers']['fishmemory']
            elif host == 'astrbot':
                entry = data
            elif host.startswith('opencode'):
                entry = data['mcp']['servers']['fishmemory'] if host.endswith('v2') else data['mcp']['fishmemory']
                self.assertEqual(entry['command'][0], python)
                self.assertEqual(entry['command'][-1], config)
                continue
            else:
                entry = data['mcpServers']['fishmemory']
            self.assertEqual(entry['command'], python)
            self.assertEqual(entry['args'][-1], config)
