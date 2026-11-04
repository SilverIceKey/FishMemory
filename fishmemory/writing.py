"""ADR-021: selected memories, local durable acknowledgement, no model calls."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from .core import Store, terms


def store_memory(config, content, topic, source_quote, mode='autonomous'):
    fields = ((content, 1200), (topic, 100), (source_quote, 4000))
    if any(not isinstance(v, str) or not v.strip() or len(v) > n for v, n in fields):
        return {'status': 'error', 'error': 'invalid_memory', 'items': []}
    if mode not in ('explicit', 'autonomous'):
        return {'status': 'error', 'error': 'invalid_mode', 'items': []}
    # Exact retry identity is scoped by the DB primary key. No fuzzy deduplication.
    payload = json.dumps([content, topic, source_quote], ensure_ascii=False)
    ident = 'memory-' + hashlib.sha256(payload.encode('utf-8')).hexdigest()
    store = None
    try:
        config.ensure_directories()
        store = Store(config.db)
        with store.db:
            store.db.execute('BEGIN IMMEDIATE')
            if store.db.execute('SELECT 1 FROM tombstones WHERE user_id=? AND agent_id=? AND event_id=?', (*config.scope, ident)).fetchone():
                return {'status': 'rejected', 'error': 'forgotten_memory', 'items': []}
            existing = store.db.execute('SELECT 1 FROM events WHERE user_id=? AND agent_id=? AND event_id=?', (*config.scope, ident)).fetchone()
            if existing:
                status = 'duplicate'
            else:
                timestamp = datetime.now(timezone.utc).isoformat()
                row = store.db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?)',
                    (*config.scope, ident, 'memory_store:' + mode, topic, content, timestamp, 'current'))
                store.db.execute('INSERT INTO event_fts(rowid,tokens) VALUES (?,?)',
                    (row.lastrowid, ' '.join(terms(topic + ' ' + content))))
                store.db.execute('INSERT INTO memory_sources VALUES (?,?,?,?,?)',
                    (*config.scope, ident, source_quote, mode))
                status = 'stored'
        # Only reached after COMMIT has succeeded.
        return {'status': status, 'persisted': True, 'items': [{'id': ident}],
                'reply_policy': 'brief_confirmation' if mode == 'explicit' else 'silent',
                'source_verification': 'caller_supplied'}
    except (sqlite3.Error, OSError):
        return {'status': 'error', 'error': 'storage_unavailable', 'persisted': False, 'items': []}
    finally:
        if store is not None:
            store.close()
