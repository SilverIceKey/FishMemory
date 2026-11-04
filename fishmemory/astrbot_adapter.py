"""Private-chat adapter for AstrBot. Host-owned identity never comes from an LLM."""
import hashlib
import json
import re
import sqlite3
import time
from urllib.error import HTTPError

from . import model_endpoint as backend, observe
from .config import _build
from .core import Store, LocalModel, recall
from .writing import store_memory


def model_settings(settings):
    return {'base_url': backend.endpoint(settings.get('baseUrl', 'http://127.0.0.1:11435/v1')),
            'api_key': settings.get('apiKey', '').strip(), 'api_key_file': settings.get('apiKeyFile', '').strip()}


def scope_config(root, event):
    kind = getattr(event.get_message_type(), 'value', None)
    group = event.get_group_id()
    if not ((kind == 'GroupMessage' and group and str(group).strip()) or
            (kind == 'FriendMessage' and not group)):
        raise ValueError('chat_identity_unavailable')
    platform, bot, sender = event.get_platform_id(), event.get_self_id(), event.get_sender_id()
    if any(v is None or not str(v).strip() for v in (platform, bot, sender)):
        raise ValueError('identity_unavailable')
    def identity(*parts):
        return hashlib.sha256(json.dumps([str(p) for p in parts]).encode()).hexdigest()
    return _build(root / 'scope.json', {'db': 'memory.sqlite', 'user_id': identity(platform, sender),
        'agent_id': identity(platform, bot)})


def check_receipt(config, ident):
    if not re.fullmatch(r'memory-[0-9a-f]{64}', ident):
        return '请提供写入回执中以 memory- 开头的完整记录 ID。'
    if not config.db.is_file():
        return '当前插件数据目录里没有记忆数据库。'
    try:
        db = sqlite3.connect(config.db.as_uri() + '?mode=ro', uri=True, timeout=2)
        try:
            present = db.execute('SELECT 1 FROM events WHERE user_id=? AND agent_id=? AND event_id=?',
                                 (*config.scope, ident)).fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return '数据库暂时无法读取。'
    return '当前账号的这条记录仍在数据库中。' if present else '当前账号范围内未找到这条记录。'


def store(config, content, topic, source_quote, mode):
    return observe.audit_call(config, content, lambda: json.dumps(
        store_memory(config, content, topic, source_quote, mode)), tool='memory_store', source='astrbot')


def read(config, settings, query):
    def operation():
        if not query.strip() or len(query) > 2000:
            return json.dumps({'status': 'error', 'items': [], 'error': 'invalid_query'})
        if not config.db.exists():
            return json.dumps({'status': 'not_found', 'items': []})
        start = time.monotonic()
        db = None
        try:
            model = model_settings(settings)
            model['model'] = backend.choose(backend.models(model), settings.get('modelId', '').strip())
            db = Store(config.db)
            result = recall(db, config.scope, query, LocalModel(model), deadline_s=config.service['recall_deadline_s'])
            observe.append_jsonl(config.service['call_log'], observe.recall_log('astrbot', config, result,
                round((time.monotonic()-start)*1000, 2), len(query)))
            return json.dumps(observe.recall_response(result), ensure_ascii=False)
        except HTTPError as exc:
            code = 'model_auth_failed' if exc.code in (401, 403) else 'model_http_error'
        except sqlite3.Error:
            code = 'database_unavailable'
        except ValueError as exc:
            code = str(exc) if str(exc) in ('invalid_base_url','multiple_models_set_modelId','no_models','configured_model_not_listed') else 'invalid_model_response'
        except (OSError, RuntimeError):
            code = 'model_unavailable'
        finally:
            if db is not None:
                db.close()
        return json.dumps({'status': 'error', 'items': [], 'error': code})
    return observe.audit_call(config, query, operation, source='astrbot')
