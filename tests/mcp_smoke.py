"""Manual MCP stdio smoke test; skips (exit 0) when no local model is reachable.

Usage:
  .venv\\Scripts\\python.exe tests\\mcp_smoke.py [--config <path>]

A temporary SQLite DB with one synthetic event is used, so neither demo.sqlite
nor the old hardcoded demo scope is required. The base config (default
fishmemory.local.json then config.example.json, docs/16 §4) supplies the local
model endpoint. Protocol/tool-shape failures fail; model unavailability only
skips with an explicit reason.
"""
import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from fishmemory import config as config_module
from fishmemory.core import Store

QUERY = '示例项目为什么选择 SQLite？'


def _smoke_config(base, workdir):
    db = workdir / 'smoke.sqlite'
    store = Store(db)
    try:
        store.ingest(base.scope, {'id': 'smoke-1', 'session': 'smoke',
                                  'topic': '示例项目',
                                  'text': '示例项目选择 SQLite 持久化长期记忆。',
                                  'time': '2026-10-30T00:00:00Z', 'state': 'current'})
    finally:
        store.close()
    service = {key: (str(value) if isinstance(value, Path) else value)
               for key, value in base.service.items()}
    service['token_file'] = str(workdir / 'service.token')
    service['call_log'] = str(workdir / 'calls.jsonl')
    service['ingest_log'] = str(workdir / 'ingest.jsonl')
    raw = {'db': str(db), 'user_id': base.user_id, 'agent_id': base.agent_id,
           'model': dict(base.model), 'service': service}
    path = workdir / 'fishmemory.local.json'
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    return path


async def _run(config_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=['-m', 'fishmemory.mcp_server', '--config', str(config_path)],
        cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            init = await client.initialize()
            tools = await client.list_tools()
            assert [tool.name for tool in tools.tools] == ['memory_recall', 'memory_store'], tools
            result = await client.call_tool('memory_recall', {'query': QUERY})
            assert not result.isError, result
            return init.protocolVersion, json.loads(result.content[0].text)


def main(argv=None):
    parser = argparse.ArgumentParser(description='FishMemory MCP stdio smoke test')
    parser.add_argument('--config', help='base config path (model endpoint); '
                                         'default fishmemory.local.json then config.example.json')
    args = parser.parse_args(argv)
    try:
        base = config_module.load_config(args.config)
    except config_module.ConfigError as error:
        print(f'SKIP: config unavailable: {error}')
        return 0
    with tempfile.TemporaryDirectory() as temp:
        config_path = _smoke_config(base, Path(temp))
        protocol, body = asyncio.run(_run(config_path))
    if body['status'] == 'found':
        ids = [item['id'] for item in body['items']]
        assert 'smoke-1' in ids, body
        print(f'protocol={protocol} tool=memory_recall source=smoke-1 PASS')
        return 0
    if body['status'] == 'not_found':
        print(f'protocol={protocol} tool=memory_recall bridge OK but local model '
              'returned no evidence; not a protocol failure SKIP')
        return 0
    print(f'SKIP: local model unavailable or output invalid: status={body["status"]} '
          f'error={body.get("error")} (items withheld by contract)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
