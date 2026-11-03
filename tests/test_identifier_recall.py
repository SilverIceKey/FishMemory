import tempfile
import unittest
from pathlib import Path

from fishmemory.core import Store, recall, query_identifiers, identifier_evidence


class WrongPlanner:
    def __init__(self):
        self.calls = []
        self.seen = []

    def plan(self, *args):
        return {'topic': '错误主题', 'keywords': ['验收'], 'historical': False}

    def select(self, query, candidates, deadline):
        self.seen = candidates
        return [c['id'] for c in candidates][:4]


class IdentifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'test.sqlite')
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.close)
        for ident, text in [
            ('answer', '青鹭1031的验收暗号是琥珀风铃726，饮品为无糖乌龙茶。'),
            ('question', '青鹭1031的验收暗号是什么？请查询记忆。'),
            ('other', '青鹭10310的验收暗号是另一条。'),
            ('unrelated', '红雀8092的验收暗号是青铜。'),
        ]:
            self.store.ingest(('u', 'a'), dict(id=ident, topic='会话', text=text,
                                              time='2026-10-31T00:00:00Z'))

    def test_original_identifier_survives_wrong_plan_and_excludes_question(self):
        model = WrongPlanner()
        result = recall(self.store, ('u', 'a'), '测试项目「青鹭1031」的验收暗号和饮品？', model)
        self.assertEqual([c['id'] for c in result['items']], ['answer'])
        self.assertEqual([c['id'] for c in model.seen], ['answer'])

    def test_missing_project_does_not_use_other_project(self):
        result = recall(self.store, ('u', 'a'), '紫鲸8392的验收暗号？', WrongPlanner())
        self.assertEqual(result['status'], 'not_found')
        self.assertEqual(result['items'], [])

    def test_scope_and_forgotten_record(self):
        for scope in [('other', 'a'), ('u', 'other')]:
            self.assertEqual(recall(self.store, scope, '青鹭1031', WrongPlanner())['items'], [])
        self.store.forget(('u', 'a'), 'answer')
        self.assertEqual(recall(self.store, ('u', 'a'), '青鹭1031', WrongPlanner())['items'], [])

    def test_offline_never_promotes_candidates_to_evidence(self):
        class Offline(WrongPlanner):
            def plan(self, *args):
                raise TimeoutError()
        result = recall(self.store, ('u', 'a'), '青鹭1031', Offline())
        self.assertEqual(result['items'], [])
        self.assertEqual([c['id'] for c in result['fallback_candidates']], ['answer'])

    def test_identifier_extraction_is_not_specific_to_test_names(self):
        self.assertEqual(query_identifiers('查询「蓝狐2048」与 AX-42'), ['蓝狐2048', 'AX-42'])
        self.assertEqual(query_identifiers('用户喜欢什么饮品？'), [])

    def test_adapter_cannot_select_unseen_id(self):
        class Unsafe(WrongPlanner):
            def select(self, *args):
                return ['unrelated']
        self.assertEqual(recall(self.store, ('u', 'a'), '青鹭1031', Unsafe())['items'], [])

    def test_cited_original_is_preferred_over_answer_echo(self):
        candidates = [
            {'id': 'dsh:session-original:8', 'text': '蓝狐2048使用SQLite。'},
            {'id': 'dsh:session-echo:20', 'text': '蓝狐2048使用SQLite，来源 dsh:session-original:8。'},
        ]
        self.assertEqual([c['id'] for c in identifier_evidence('蓝狐2048', candidates)],
                         ['dsh:session-original:8'])


if __name__ == '__main__':
    unittest.main()

