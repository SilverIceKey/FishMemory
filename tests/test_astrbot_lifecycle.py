"""SDK-shape/lifecycle contract tests; not a running AstrBot or real-model test."""
import asyncio
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import fishmemory
from fishmemory import astrbot_adapter as bridge
from fishmemory.core import Store
from fishmemory.summary_index import originals


def complete(coroutine):
    # All I/O awaits below are replaced by immediately-completing test adapters.
    # This also works in CI sandboxes that forbid event-loop socketpair creation.
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError('unexpected suspended coroutine')


async def inline(function, *args, **kwargs):
    return function(*args, **kwargs)


class LifecycleTests(unittest.TestCase):
    def test_reload_lifecycle_and_host_message_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            modules={name: types.ModuleType(name) for name in (
                'astrbot','astrbot.api','astrbot.api.event','astrbot.api.provider','astrbot.api.star',
                'plugin_contract')}
            modules['plugin_contract'].__path__=[str(Path(__file__).resolve().parents[1])]
            modules['plugin_contract.fishmemory']=fishmemory
            logger=types.SimpleNamespace(info=lambda *a:None, warning=lambda *a:None)
            modules['astrbot.api'].logger=logger
            decorate=lambda *a,**kw: lambda f:f
            modules['astrbot.api.event'].filter=types.SimpleNamespace(
                on_llm_request=decorate, command=decorate, permission_type=decorate,
                llm_tool=decorate, PermissionType=types.SimpleNamespace(ADMIN='admin'))
            modules['astrbot.api.event'].AstrMessageEvent=object
            modules['astrbot.api.provider'].ProviderRequest=object
            class Star:
                def __init__(self,*args): pass
            modules['astrbot.api.star'].Star=Star
            modules['astrbot.api.star'].Context=object
            modules['astrbot.api.star'].StarTools=types.SimpleNamespace(get_data_dir=lambda name:root)
            class Settings(dict):
                schema={'modelId':{}}
            class Task:
                joined=False
                def done(self): return False
                def __await__(self):
                    self.joined=True
                    if False: yield
            tasks=[]
            def schedule(coroutine):
                coroutine.close()
                task=Task();tasks.append(task)
                return task
            class Event:
                message_obj=types.SimpleNamespace(message_id='m42')
                def get_message_str(self): return '记住：我晚上不喝咖啡。\n会睡不着。'
                def get_message_type(self): return types.SimpleNamespace(value='FriendMessage')
                def get_group_id(self): return ''
                def get_platform_id(self): return 'qq'
                def get_self_id(self): return 'bot'
                def get_sender_id(self): return 'alice'
            spec=importlib.util.spec_from_file_location('plugin_contract.main', Path(__file__).resolve().parents[1]/'main.py')
            module=importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules, modules), patch.object(asyncio,'to_thread',inline), patch.object(asyncio,'create_task',schedule):
                spec.loader.exec_module(module)
                with patch.object(module.backend,'models',return_value=['qwen3.5-0.8b']):
                    plugin=module.FishMemoryPlugin(None,Settings())
                    complete(plugin.initialize())
                    complete(plugin.initialize())
                    self.assertEqual(len(tasks),1)
                    request=types.SimpleNamespace(system_prompt='existing persona',
                        func_tool=types.SimpleNamespace(tools=[types.SimpleNamespace(name='memory_recall')]))
                    complete(plugin.apply_memory_voice(Event(),request))
                    complete(plugin.apply_memory_voice(Event(),request))
                    self.assertEqual(request.system_prompt,'\n\n'.join(
                        ('existing persona',module.PERSONA,module.RECALL_POLICY,module.STORE_POLICY)))
                    saved=json.loads(complete(plugin.memory_store(Event(),'晚上避免咖啡','错主题','我晚上不喝咖啡。','explicit')))
                    cfg=module.bridge.scope_config(root,Event())
                    db=Store(cfg.db)
                    try:
                        item=originals(db,cfg.scope,[saved['items'][0]['id']])[0]
                        self.assertEqual(item['text'],Event().get_message_str())
                        self.assertEqual(item['source_verification'],'host_message')
                    finally:
                        db.close()
                    complete(plugin.terminate())
                    self.assertTrue(tasks[0].joined)
                    self.assertTrue(plugin._index_stop.is_set())
                    reloaded=module.FishMemoryPlugin(None,Settings())
                    self.assertEqual(module.bridge.scope_config(reloaded.root,Event()).db,cfg.db)
                    self.assertIn('仍在',bridge.check_receipt(cfg,saved['items'][0]['id']))


if __name__=='__main__': unittest.main()
