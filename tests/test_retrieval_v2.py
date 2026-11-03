import tempfile
import time
import unittest
from pathlib import Path
from fishmemory.core import Store
from fishmemory.retrieval_v2 import broad_candidates,select_each,recall_v2


class V2Tests(unittest.TestCase):
    def test_wrong_model_topic_cannot_hide_original_hits(self):
        with tempfile.TemporaryDirectory() as td:
            s=Store(Path(td)/'db')
            try:
                s.ingest(('u','a'),dict(id='1',topic='甲',text='SQLite',time='2026-01-01',state='historical'))
                s.ingest(('other','a'),dict(id='2',topic='甲',text='SQLite',time='2026-01-01'))
                found=broad_candidates(s,('u','a'),'SQLite',dict(topic='乙',keywords=['不存在'],historical=False))
                self.assertEqual([c['id'] for c in found],['1'])
            finally: s.close()

    def test_fabricated_quote_rejected(self):
        class Fake:
            def ask(self,*args):return dict(supports=True,quote='编造内容')
        ids,judgments=select_each(Fake(),'问题',[dict(id='1',text='来源原文')],time.monotonic()+1)
        self.assertEqual(ids,[])
        self.assertFalse(judgments[0]['accepted'])

    def test_non_boolean_not_truthy(self):
        class Fake:
            def ask(self,*args):return dict(supports='false',quote='原文')
        with self.assertRaises(ValueError):
            select_each(Fake(),'问题',[dict(id='1',text='原文')],time.monotonic()+1)

    def test_failure_returns_unconfirmed_candidates(self):
        class Fake:
            calls=[]
            def plan(self,*args):raise TimeoutError()
        with tempfile.TemporaryDirectory() as td:
            s=Store(Path(td)/'db')
            try:
                result=recall_v2(s,('u','a'),'问题',Fake())
                self.assertEqual(result['items'],[])
                self.assertEqual(result['error'],'TimeoutError')
            finally:s.close()

    def test_batch_selection_uses_scoped_candidates(self):
        class Fake:
            calls=[]
            def plan(self,*args):return dict(keywords=['Python'],topic='错误主题',historical=False)
            def select(self,query,candidates,deadline):return [c['id'] for c in candidates]
        with tempfile.TemporaryDirectory() as td:
            s=Store(Path(td)/'db')
            try:
                s.ingest(('u','a'),dict(id='1',topic='偏好',text='Python',time='2026-01-01'))
                s.ingest(('other','a'),dict(id='2',topic='偏好',text='Python',time='2026-01-01'))
                result=recall_v2(s,('u','a'),'Python',Fake(),selection='batch')
                self.assertEqual([c['id'] for c in result['items']],['1'])
            finally:s.close()

if __name__=='__main__': unittest.main()

