"""Real AstrBot SDK + actual llama.cpp, synthetic private messages; no bot account needed.

Use a disposable AstrBot 4.14.6 environment: python tests/astrbot_smoke.py <config.json>.
"""
import asyncio
import importlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


async def run(config, tmp, distribution=None):
    plugin = tmp / 'data/plugins/FishMemory'
    os.environ['ASTRBOT_ROOT'] = str(tmp)
    sys.path.insert(0, str(tmp))
    plugin.parent.mkdir(parents=True)
    if distribution:
        from astrbot.api.event import filter  # initialize the public SDK before its updater
        from astrbot.core.star.updator import PluginUpdator
        installer = PluginUpdator()
        if distribution == '--repository':
            plugin = Path(await asyncio.wait_for(installer.install('https://github.com/SilverIceKey/FishMemory'), timeout=40))
        else:
            archive = tmp / 'package.zip'
            shutil.copy2(distribution, archive)
            installer.unzip_file(str(archive), str(plugin))
    else:
        plugin.mkdir()
        for filename in ('main.py', 'metadata.yaml', '_conf_schema.json'):
            shutil.copy2(ROOT / filename, plugin / filename)
        shutil.copytree(ROOT / 'fishmemory', plugin / 'fishmemory', ignore=shutil.ignore_patterns('__pycache__'))
    module = importlib.import_module(f'data.plugins.{plugin.name}.main')
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.star import star_map
    assert star_map[module.__name__].star_cls_type is module.FishMemoryPlugin
    from astrbot.core.star.register.star_handler import llm_tools
    tools = {t.name: t for t in llm_tools.func_list if t.name in ('memory_store', 'memory_recall')}
    assert set(tools) == {'memory_store', 'memory_recall'}, tools
    assert set(tools['memory_store'].parameters['properties']) == {'content', 'topic', 'source_quote', 'mode'}
    host_settings = {'baseUrl': config['base_url'], 'apiKeyFile': config['api_key_file'], 'modelId': ''}
    adapter = module.FishMemoryPlugin(None, host_settings)
    ids = await asyncio.to_thread(module.backend.models, module.bridge.model_settings(host_settings))
    selected = module.backend.choose(ids, '')
    event = module.AstrMessageEvent.__new__(module.AstrMessageEvent)
    event.platform_meta = SimpleNamespace(id='smoke-platform')
    event.message_obj = SimpleNamespace(type=MessageType.FRIEND_MESSAGE, group_id='', self_id='test-bot',
                                        sender=SimpleNamespace(user_id='test-alice'))
    from astrbot.api.provider import ProviderRequest
    request = ProviderRequest(system_prompt='原有人格')
    await adapter.apply_memory_voice(event, request)
    await adapter.apply_memory_voice(event, request)
    assert request.system_prompt == '原有人格\n\n' + module.PERSONA
    text = '茶铺风铃5826每周二休息。'
    first = json.loads(await adapter.memory_store(event, text, '风铃5826', text, 'explicit'))
    assert first.get('persisted'), first
    second = module.FishMemoryPlugin(None, host_settings)
    event.message_obj.type = MessageType.GROUP_MESSAGE
    event.message_obj.group_id = 'test-group'
    found = json.loads(await second.memory_recall(event, '风铃5826 每周休息日'))
    assert first['items'][0]['id'] in {i['id'] for i in found['items']}, found
    print(f'PASS AstrBot native tool registration, authenticated model discovery ({selected}), persisted write and recall after plugin re-creation.')


if __name__ == '__main__':
    path = Path(sys.argv[1]).resolve()
    config = json.loads(path.read_text(encoding='utf-8-sig'))['model']
    config['api_key_file'] = str((path.parent / config['api_key_file']).resolve())
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        asyncio.run(run(config, Path(temp), sys.argv[2] if len(sys.argv) > 2 else None))
