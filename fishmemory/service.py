"""Local-only HTTP memory service (contract: docs/16-DSH原生插件集成.md §5).

Stdlib only. Binds 127.0.0.1, requires a bearer token on every path except
/healthz, opens an independent SQLite connection per request, and answers with
a unified JSON error shape. Scope (user_id, agent_id) is forced from config;
request bodies may not select it (docs/16 §4.3).

`python -m fishmemory serve --config <path>` starts this server.
"""
import hmac
import json
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import observe
from .core import LocalModel, Store, recall

VERSION = 1
EVENT_STATES = ('current', 'historical', 'disputed')
EVENT_TEXT_LIMIT = 20000
BODY_DRAIN_LIMIT = 1024 * 1024
REQUEST_TIMEOUT_S = 5
_POST_PATHS = ('/v1/events', '/v1/recall', '/v1/forget')


class HttpError(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status = status
        self.code = code


def _is_busy(error):
    message = str(error).lower()
    return 'locked' in message or 'busy' in message


def _normalize_event(item):
    """Validate one event; return (event, None) or (None, error_code)."""
    if not isinstance(item, dict):
        return None, 'invalid_event'
    event_id = item.get('id')
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 200:
        return None, 'invalid_event'
    session = item.get('session')
    topic = item.get('topic')
    if not isinstance(session, str) or len(session) > 200:
        return None, 'invalid_event'
    if not isinstance(topic, str) or len(topic) > 200:
        return None, 'invalid_event'
    occurred_at = item.get('time')
    if not isinstance(occurred_at, str) or not 1 <= len(occurred_at) <= 64:
        return None, 'invalid_event'
    text = item.get('text')
    if not isinstance(text, str) or not 1 <= len(text) <= EVENT_TEXT_LIMIT or not text.strip():
        return None, 'invalid_event'
    state = item.get('state', 'current')
    if state not in EVENT_STATES:
        return None, 'invalid_state'
    return {
        'id': event_id,
        'session': session,
        'topic': topic,
        'text': text,
        'time': occurred_at,
        'state': state,
    }, None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'FishMemory/0.1'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(REQUEST_TIMEOUT_S)

    def log_message(self, format, *args):
        # Request logs are suppressed so headers (and any token mistake) never
        # land in process output. Operational logs are the JSONL files.
        pass

    def _path(self):
        return self.path.split('?', 1)[0]

    def _authorized(self):
        header = self.headers.get('Authorization', '')
        supplied = header[7:].strip() if header.startswith('Bearer ') else ''
        try:
            return bool(supplied) and hmac.compare_digest(
                supplied.encode('utf-8'), self.server.token.encode('utf-8'))
        except (TypeError, ValueError):
            return False

    def _send_json(self, status, body):
        # Drain any still-unread request body before closing. Windows sends a
        # TCP reset when a socket closes with unread data, which can hide a
        # legitimate 413/401 response from the client.
        self._discard_body()
        raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Connection', 'close')
        if status == 401:
            self.send_header('WWW-Authenticate', 'Bearer')
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(raw)
        except OSError:
            pass

    def _send_error(self, status, code):
        self._send_json(status, {'status': 'error', 'error': code})

    def _discard_body(self):
        if getattr(self, '_body_consumed', False):
            return
        self._body_consumed = True
        try:
            length = int(self.headers.get('Content-Length'))
        except (TypeError, ValueError):
            return
        remaining = min(max(length, 0), BODY_DRAIN_LIMIT)
        while remaining > 0:
            try:
                chunk = self.rfile.read(min(remaining, 65536))
            except OSError:
                break
            if not chunk:
                break
            remaining -= len(chunk)

    def do_GET(self):
        self._body_consumed = False
        path = self._path()
        if path == '/healthz':
            return self._healthz()
        if not self._authorized():
            return self._send_error(401, 'unauthorized')
        if path in _POST_PATHS:
            return self._send_error(405, 'method_not_allowed')
        return self._send_error(404, 'not_found')

    def do_POST(self):
        self._body_consumed = False
        path = self._path()
        if not self._authorized():
            return self._send_error(401, 'unauthorized')
        if path not in _POST_PATHS:
            return self._send_error(404, 'not_found')
        handler = getattr(self, '_' + path.rsplit('/', 1)[-1], None)
        try:
            handler()
        except HttpError as error:
            self._send_error(error.status, error.code)
        except sqlite3.OperationalError as error:
            if _is_busy(error):
                self._send_error(503, 'database_busy')
            else:
                self._send_error(500, 'database_error')
        except sqlite3.Error:
            self._send_error(500, 'database_error')
        except OSError:
            self._send_error(500, 'service_error')
        except Exception:
            # Never leak exception text (may contain paths or key material).
            self._send_error(500, 'internal_error')

    def _healthz(self):
        # Unauthenticated liveness only; token is never echoed (docs/16 §5).
        self._send_json(200, {'status': 'ok', 'version': VERSION,
                              'db': str(self.server.config.db)})

    def _read_json(self):
        length_header = self.headers.get('Content-Length')
        if length_header is None:
            raise HttpError(400, 'invalid_request')
        try:
            length = int(length_header)
        except ValueError:
            raise HttpError(400, 'invalid_request')
        if length < 0:
            raise HttpError(400, 'invalid_request')
        if length > self.server.config.service['max_body_bytes']:
            raise HttpError(413, 'payload_too_large')
        self._body_consumed = True
        try:
            raw = self.rfile.read(length)
        except TimeoutError:
            raise HttpError(408, 'request_timeout')
        try:
            return json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HttpError(400, 'invalid_json')

    def _events(self):
        config = self.server.config
        body = self._read_json()
        events = body.get('events') if isinstance(body, dict) else None
        if not isinstance(events, list):
            raise HttpError(400, 'invalid_request')
        if len(events) > config.service['max_events']:
            raise HttpError(413, 'too_many_events')
        store = Store(config.db)
        try:
            results = []
            for item in events:
                event, code = _normalize_event(item)
                if code:
                    event_id = item.get('id') if isinstance(item, dict) else None
                    results.append({'id': event_id, 'status': 'error', 'error': code})
                    observe.append_jsonl(
                        config.service['ingest_log'],
                        observe.ingest_log(config, event_id, None, 'error', code))
                    continue
                error = None
                try:
                    status = store.ingest(config.scope, event)
                except ValueError as problem:
                    error = ('conflicting_event_id' if 'conflict' in str(problem).lower()
                             else 'invalid_event')
                    status = 'error'
                entry = {'id': event['id'], 'status': status}
                if error:
                    entry['error'] = error
                results.append(entry)
                observe.append_jsonl(
                    config.service['ingest_log'],
                    observe.ingest_log(config, event['id'], event['session'], status, error))
        finally:
            store.close()
        self._send_json(200, {'results': results})

    def _recall(self):
        config = self.server.config
        body = self._read_json()
        if not isinstance(body, dict):
            raise HttpError(400, 'invalid_request')
        query = body.get('query')
        if not isinstance(query, str) or not 1 <= len(query) <= 2000:
            raise HttpError(400, 'invalid_query')
        max_rounds = body.get('max_rounds', 2)
        if type(max_rounds) is not int or not 1 <= max_rounds <= 3:
            raise HttpError(400, 'invalid_request')
        if not config.db.exists():
            # docs/06: no database means service error, not "no history".
            raise HttpError(503, 'database_unavailable')
        started = time.monotonic()
        store = Store(config.db)
        try:
            result = recall(store, config.scope, query, LocalModel(config.model),
                            max_rounds=max_rounds,
                            deadline_s=config.service['recall_deadline_s'])
        finally:
            store.close()
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        observe.append_jsonl(config.service['call_log'],
                             observe.recall_log('http', config, result, elapsed_ms, len(query)))
        # Model failure is a business result (partial/error) and stays HTTP 200;
        # transient storage failures are raised above and become 5xx.
        self._send_json(200, observe.recall_response(result))

    def _forget(self):
        config = self.server.config
        body = self._read_json()
        if not isinstance(body, dict):
            raise HttpError(400, 'invalid_request')
        event_id = body.get('id')
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 200:
            raise HttpError(400, 'invalid_request')
        # Even an absent id gets a tombstone so a later replay cannot resurrect
        # it (docs/16 §5); a fresh database is created for that audit state.
        store = Store(config.db)
        try:
            status = 'forgotten' if store.details(config.scope, [event_id]) else 'absent'
            store.forget(config.scope, event_id)
        finally:
            store.close()
        observe.append_jsonl(config.service['call_log'],
                             observe.forget_log(config, event_id, status))
        self._send_json(200, {'status': status})


class ServiceServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, config, token):
        self.config = config
        self.token = token
        super().__init__((config.service['host'], config.service['port']), _Handler)

    @property
    def url(self):
        host, port = self.server_address[:2]
        return f'http://{host}:{port}'


def create_server(config):
    """Prepare directories/token and bind the service socket."""
    config.ensure_directories()
    token = config.ensure_token()
    return ServiceServer(config, token)


def run_service(config):
    server = create_server(config)
    # stdout is local operator output: URL/paths only, never the token.
    print(f'FishMemory service listening on {server.url} db={config.db} '
          f'token_file={config.service["token_file"]}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

