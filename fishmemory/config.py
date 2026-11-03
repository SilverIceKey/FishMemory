"""Local configuration contract for the FishMemory service (docs/16 §4).

Scope (user_id, agent_id) is read only from this file or the CLI. HTTP request
bodies and MCP tool arguments never carry it. Relative paths resolve against
the directory of the configuration file. Legacy flat model configs remain
accepted (docs/16 §4.2). Defaults and field names are project design decisions,
not DSH/MCP/Qwen official fields.
"""
import copy
import json
import os
import secrets
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_NAME = 'fishmemory.local.json'
EXAMPLE_CONFIG_NAME = 'config.example.json'
# Project design decision (not an official DSH/MCP variable): optional override
# for the config path, equivalent to passing --config.
CONFIG_ENV = 'FISHMEMORY_CONFIG'

# docs/16 §4 default values, minus the machine-specific api_key_file path.
DEFAULTS = {
    'db': '.fishmemory/memory.sqlite',
    'user_id': 'local',
    'agent_id': 'dsh',
    'model': {
        'base_url': 'http://127.0.0.1:11435/v1',
        'model': 'qwen3.5-0.8b',
        'api_key_file': '',
    },
    'service': {
        'host': '127.0.0.1',
        'port': 8765,
        'token_file': '.fishmemory/service.token',
        'call_log': '.fishmemory/mcp-calls.jsonl',
        'ingest_log': '.fishmemory/ingest.jsonl',
        'max_body_bytes': 262144,
        'max_events': 64,
        'recall_deadline_s': 20,
    },
}

_LOOPBACK_HOSTS = ('127.0.0.1', 'localhost')


class ConfigError(ValueError):
    """Invalid or missing local configuration."""


def _resolve(base_dir, value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _text(raw, key, default, limit=200):
    value = raw.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f'{key} must be a non-empty string')
    value = value.strip()
    if len(value) > limit:
        raise ConfigError(f'{key} is too long')
    return value


def _integer(raw, key, default, minimum, maximum):
    value = raw.get(key, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ConfigError(f'{key} must be an integer in [{minimum}, {maximum}]')
    return value


def _number(raw, key, default, minimum, maximum):
    value = raw.get(key, default)
    if type(value) not in (int, float) or not minimum <= value <= maximum:
        raise ConfigError(f'{key} must be a number in [{minimum}, {maximum}]')
    return float(value)


class Config:
    """Resolved local config. Paths are absolute; scope comes only from here."""

    def __init__(self, source, base_dir, db, user_id, agent_id, model, service):
        self.source = source
        self.base_dir = base_dir
        self.db = db
        self.user_id = user_id
        self.agent_id = agent_id
        self.model = model
        self.service = service

    @property
    def scope(self):
        return (self.user_id, self.agent_id)

    def ensure_token(self):
        return ensure_token(self.service['token_file'])

    def ensure_directories(self):
        for path in (self.db, self.service['token_file'],
                     self.service['call_log'], self.service['ingest_log']):
            path.parent.mkdir(parents=True, exist_ok=True)


def resolve_config_path(explicit=None):
    """Return the config file to use: --config/env, else local, else example."""
    candidates = []
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_absolute():
            path = path.resolve()
            if not path.is_file():
                raise ConfigError(f'config file not found: {path}')
            return path
        # A relative --config is tried against cwd first, then the repository
        # root, so host integrations can launch from any working directory.
        for base in (Path.cwd(), REPO_ROOT):
            candidate = (base / path).resolve()
            if candidate.is_file() and candidate not in candidates:
                candidates.append(candidate)
        if not candidates:
            raise ConfigError(f'config file not found: {explicit} '
                              f'(tried cwd and {REPO_ROOT})')
        return candidates[0]
    env = os.environ.get(CONFIG_ENV)
    if env:
        return resolve_config_path(env)
    for base in (Path.cwd(), REPO_ROOT):
        for name in (DEFAULT_CONFIG_NAME, EXAMPLE_CONFIG_NAME):
            candidate = (base / name).resolve()
            if candidate.is_file() and candidate not in candidates:
                candidates.append(candidate)
    if not candidates:
        raise ConfigError(
            'no config file found; pass --config or create '
            f'{DEFAULT_CONFIG_NAME} (searched cwd and {REPO_ROOT})')
    return candidates[0]


def _parse_model(raw, base_dir):
    nested = raw.get('model')
    if isinstance(nested, dict):
        source = nested
    elif any(key in raw for key in ('base_url', 'model', 'api_key_file')):
        # docs/16 §4.2: legacy flat model config is treated as the model object.
        source = {key: raw[key] for key in ('base_url', 'model', 'api_key_file')
                  if key in raw}
    else:
        source = {}
    base_url = source.get('base_url', DEFAULTS['model']['base_url'])
    if not isinstance(base_url, str) or not base_url.startswith(('http://', 'https://')):
        raise ConfigError('model.base_url must be an http(s) URL')
    name = source.get('model', DEFAULTS['model']['model'])
    if not isinstance(name, str) or not name.strip():
        raise ConfigError('model.model must be a non-empty string')
    key_file = source.get('api_key_file', DEFAULTS['model']['api_key_file'])
    if not isinstance(key_file, str):
        raise ConfigError('model.api_key_file must be a string')
    return {
        'base_url': base_url.rstrip('/'),
        'model': name.strip(),
        # Empty string means "no key file configured"; LocalModel then fails
        # closed into partial/error instead of fabricating memories.
        'api_key_file': str(_resolve(base_dir, key_file)) if key_file.strip() else '',
    }


def _parse_service(raw, base_dir):
    if not isinstance(raw, dict):
        raise ConfigError('service must be an object')
    host = raw.get('host', DEFAULTS['service']['host'])
    if host not in _LOOPBACK_HOSTS:
        raise ConfigError('service.host must be 127.0.0.1 (local-only service)')
    defaults = DEFAULTS['service']
    return {
        'host': '127.0.0.1',
        'port': _integer(raw, 'port', defaults['port'], 0, 65535),
        'token_file': _resolve(base_dir, _text(raw, 'token_file', defaults['token_file'])),
        'call_log': _resolve(base_dir, _text(raw, 'call_log', defaults['call_log'])),
        'ingest_log': _resolve(base_dir, _text(raw, 'ingest_log', defaults['ingest_log'])),
        'max_body_bytes': _integer(raw, 'max_body_bytes', defaults['max_body_bytes'], 64, 16 * 1024 * 1024),
        'max_events': _integer(raw, 'max_events', defaults['max_events'], 1, 10000),
        'recall_deadline_s': _number(raw, 'recall_deadline_s', defaults['recall_deadline_s'], 0.1, 30),
    }


def _build(source, raw):
    if not isinstance(raw, dict):
        raise ConfigError('config root must be a JSON object')
    base_dir = source.parent if source is not None else Path.cwd()
    db = _resolve(base_dir, _text(raw, 'db', DEFAULTS['db'], limit=1000))
    user_id = _text(raw, 'user_id', DEFAULTS['user_id'])
    agent_id = _text(raw, 'agent_id', DEFAULTS['agent_id'])
    model = _parse_model(raw, base_dir)
    service = _parse_service(raw.get('service', {}), base_dir)
    return Config(source, base_dir, db, user_id, agent_id, model, service)


def load_config(path=None):
    source = resolve_config_path(path)
    try:
        text = source.read_text(encoding='utf-8-sig')
    except OSError as error:
        raise ConfigError(f'cannot read config {source}: {error}') from error
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        raise ConfigError(f'invalid JSON in {source}: {error}') from error
    return _build(source, raw)


def ensure_token(token_file):
    """Read the bearer token, generating a random 32-byte one if absent.

    Best-effort 0600 permissions; the token itself is never logged or printed
    (docs/16 §4.4-4.5).
    """
    path = Path(token_file)
    try:
        existing = path.read_text(encoding='utf-8').strip()
    except FileNotFoundError:
        existing = ''
    except OSError as error:
        raise ConfigError(f'cannot read token file {path}: {error}') from error
    if existing:
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # A concurrent writer, or a stale empty file: prefer non-empty content.
        try:
            existing = path.read_text(encoding='utf-8').strip()
        except OSError:
            existing = ''
        if existing:
            return existing
        path.write_text(token, encoding='utf-8')
    except OSError as error:
        raise ConfigError(f'cannot write token file {path}: {error}') from error
    else:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(token)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def default_config_dict():
    return copy.deepcopy(DEFAULTS)


def init_config(path=None, force=False):
    """Write a local config from the template and create its token file.

    The on-disk template (config.example.json) is copied when present; it holds
    a key *file path*, never key material. Existing configs are not overwritten
    unless force=True.
    """
    if path:
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = Path.cwd() / target
        target = target.resolve()
    else:
        target = (REPO_ROOT / DEFAULT_CONFIG_NAME).resolve()
    if target.exists() and not force:
        raise ConfigError(f'config already exists: {target} (use --force to overwrite)')
    target.parent.mkdir(parents=True, exist_ok=True)
    template = (REPO_ROOT / EXAMPLE_CONFIG_NAME).resolve()
    raw = default_config_dict()
    if template.is_file() and template != target:
        try:
            loaded = json.loads(template.read_text(encoding='utf-8-sig'))
            if isinstance(loaded, dict):
                raw = loaded
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigError(f'cannot read template {template}: {error}') from error
    target.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + '\n',
                      encoding='utf-8')
    config = load_config(target)
    config.ensure_directories()
    config.ensure_token()
    return config

