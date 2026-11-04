"""Installed-wheel test: separate MCP processes, fresh cwd, real local model.

Run after pip install dist/fishmemory_local-0.2.0-py3-none-any.whl.
Only a temporary synthetic memory DB is written. Failure is not a skipped test.
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from fishmemory.config import load_config


async def invoke(config, cwd, tool, arguments):
    params = StdioServerParameters(command=sys.executable,
        args=['-m', 'fishmemory.mcp_server', '--config', str(config)], cwd=str(cwd))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            names = {t.name for t in (await client.list_tools()).tools}
            assert names == {'memory_store', 'memory_recall'}, names
            response = await client.call_tool(tool, arguments)
            assert not response.isError, response
            return json.loads(response.content[0].text)


async def run(base_path):
    base = load_config(base_path)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = root / 'config.json'
        config.write_text(json.dumps({'user_id': 'synthetic', 'agent_id': 'portable',
            'model': base.model}), encoding='utf-8')
        data = {'content': '白鹭8642小店每周五休息，招牌饮品是杏仁奶。',
                'topic': '白鹭8642', 'source_quote': '记一下，白鹭8642小店每周五休息，招牌饮品是杏仁奶。',
                'mode': 'explicit'}
        first = await invoke(config, root, 'memory_store', data)
        assert first['persisted'] and first['status'] == 'stored', first
        second = await invoke(config, root, 'memory_store', data)
        assert second['status'] == 'duplicate', second
        recall = await invoke(config, root, 'memory_recall', {'query': '白鹭8642 每周休息日 招牌饮品'})
        assert recall['status'] == 'found', recall
        assert first['items'][0]['id'] in [i['id'] for i in recall['items']], recall
        assert all(b['provenance']['audit_logged'] for b in (first, second, recall))
        logs = [json.loads(line) for line in (root / '.fishmemory/mcp-calls.jsonl').read_text(encoding='utf-8').splitlines()]
        metric = next(row for row in logs if 'calls' in row)
        print(json.dumps({'result': 'PASS', 'processes': 3, 'write': first['status'],
            'retry': second['status'], 'recall': recall['status'],
            'local_model_calls': metric['calls'], 'recall_elapsed_ms': metric['elapsed_ms']}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(run(sys.argv[1]))
