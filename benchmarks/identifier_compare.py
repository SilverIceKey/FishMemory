"""Synthetic targeted regression comparison, not an independent quality score."""
import json
import statistics
import tempfile
from pathlib import Path

from fishmemory.config import load_config
from fishmemory.core import Store, LocalModel, recall, recall_legacy


def main():
    config = load_config()
    rows = []
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / 'test.sqlite')
        try:
            events = [
                ('fact', '青鹭1031的验收暗号是琥珀风铃726，饮品为无糖乌龙茶。'),
                ('question', '测试项目「青鹭1031」的验收暗号和饮品是什么？请查询记忆。'),
                ('neighbor', '青鹭10310的验收暗号是紫铜旋钮，饮品为咖啡。'),
                ('other', '红雀8092的验收暗号是青铜，饮品为豆浆。'),
                ('new', '蓝狐2048的存储方案是SQLite，原因是现场断网。'),
                ('newq', '蓝狐2048用什么存储？'),
            ]
            for ident, text in events:
                store.ingest(('test', 'test'), dict(id=ident, topic='会话', text=text,
                                                   time='2026-10-31T00:00:00Z'))
            cases = [
                ('long', '测试项目「青鹭1031」的验收暗号和饮品', ['fact']),
                ('short', '青鹭1031', ['fact']),
                ('missing_long', '紫鲸8392 验收暗号', []),
                ('missing_short', '紫鲸8392', []),
                ('new_fact', '蓝狐2048为什么用SQLite？', ['new']),
                ('new_missing', '绿鹿5120的存储方案？', []),
            ]
            for name, query, gold in cases:
                row = dict(case=name, query=query, gold=gold)
                for label, fn in [('before', recall_legacy), ('after', recall)]:
                    result = fn(store, ('test', 'test'), query, LocalModel(config.model))
                    row[label] = result
                    row[label]['exact'] = (not result.get('error') and
                        set(c['id'] for c in result['items']) == set(gold))
                rows.append(row)
                print(name, row['before']['exact'], row['after']['exact'], flush=True)
        finally:
            store.close()
    summary = {}
    for label in ['before', 'after']:
        calls = [c for row in rows for c in row[label]['calls']]
        summary[label] = dict(exact=sum(row[label]['exact'] for row in rows), cases=len(rows),
            calls=len(calls), input_tokens=sum(c['usage'].get('prompt_tokens', 0) for c in calls),
            output_tokens=sum(c['usage'].get('completion_tokens', 0) for c in calls),
            median_ms=statistics.median(row[label]['elapsed_ms'] for row in rows))
    Path('reports/identifier-comparison.json').write_text(json.dumps(
        dict(summary=summary, rows=rows, online_calls=0), ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()

