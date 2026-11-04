"""OpenAI-compatible llama.cpp discovery. Never redirect authenticated requests."""
import json
from pathlib import Path
import urllib.request
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


def endpoint(url):
    parts = urlsplit(url.strip())
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ValueError('invalid_base_url')
    return url.strip().rstrip('/') + ('' if parts.path.strip('/') else '/v1')


def headers(model):
    key = model.get('api_key', '')
    if not key and model.get('api_key_file'):
        key = Path(model['api_key_file']).read_text(encoding='utf-8-sig').strip()
    elif not key and 'api_key' not in model:
        raise ValueError('missing_model_credentials')
    return {'Authorization': 'Bearer ' + key} if key else {}


def models(model):
    request = urllib.request.Request(endpoint(model['base_url']) + '/models', headers=headers(model))
    with opener().open(request, timeout=5) as response:
        raw = response.read(262145)
    if len(raw) > 262144:
        raise ValueError('invalid_model_list')
    data = json.loads(raw)
    rows = data.get('data') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError('invalid_model_list')
    ids = list(dict.fromkeys(row['id'] for row in rows if isinstance(row, dict)
        and isinstance(row.get('id'), str) and 0 < len(row['id']) <= 256 and row['id'].isprintable()))
    if not ids:
        raise ValueError('no_models')
    return ids


def choose(ids, requested):
    if requested:
        if requested not in ids:
            raise ValueError('configured_model_not_listed')
        return requested
    if len(ids) != 1:
        raise ValueError('multiple_models_set_modelId')
    return ids[0]
