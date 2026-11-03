"""Local-only snapshot verification. Reports IDs/metrics, never raw history."""
import json
import sqlite3
import tempfile
from pathlib import Path
from fishmemory.config import load_config
from fishmemory.core import Store, LocalModel, recall


def main():
    cfg = load_config()
    rows = []
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'snapshot.sqlite'
        source = sqlite3.connect(cfg.db.as_uri() + '?mode=ro', uri=True)
        dest = sqlite3.connect(path)
        try:
            source.backup(dest)
        finally:
            source.close()
            dest.close()
        store = Store(path)
        try:
            for query in ['测试项目「青鹭1031」的验收暗号和饮品', '青鹭1031',
                          '紫鲸8392 验收暗号', '紫鲸8392']:
                result = recall(store, cfg.scope, query, LocalModel(cfg.model))
                row = dict(query=query, status=result['status'],
                           ids=[c['id'] for c in result['items']],
                           candidate_ids=result.get('candidate_ids'),
                           calls=result['calls'], elapsed_ms=result['elapsed_ms'])
                rows.append(row)
                print(query, row['status'], row['ids'], flush=True)
        finally:
            store.close()
    Path('reports/identifier-live-snapshot.json').write_text(
        json.dumps(dict(rows=rows, online_calls=0), ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()

