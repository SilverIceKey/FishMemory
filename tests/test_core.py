import tempfile
import unittest
from pathlib import Path
from fishmemory.core import Store, LocalModel, recall
import time


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'test.sqlite'
        self.s=Store(self.path)
        self.event={'id':'e1','topic':'偏好','text':'用户喜欢Python','time':'2026-10-01T00:00:00Z'}
        self.s.ingest(('u','a'),self.event)

    def tearDown(self):
        self.s.close();self.temp.cleanup()

    def test_persistence_and_session_independence(self):
        self.s.close();self.s=Store(self.path)
        self.assertEqual(self.s.details(('u','a'),['e1'])[0]['text'],self.event['text'])
        self.assertTrue(self.s.search(('u','a'),'Python'))

    def test_scope(self):
        for scope in [('other','a'),('u','other')]:
            self.assertEqual(self.s.details(scope,['e1']),[])
            self.assertEqual(self.s.search(scope,'Python'),[])
            self.assertEqual(self.s.topics(scope),[])

    def test_idempotency(self):
        self.assertEqual(self.s.ingest(('u','a'),self.event),'duplicate')
        with self.assertRaises(ValueError):
            self.s.ingest(('u','a'),dict(self.event,text='different'))

    def test_forget_replay(self):
        self.s.forget(('u','a'),'e1')
        self.assertEqual(self.s.search(('u','a'),'Python'),[])
        self.assertEqual(self.s.ingest(('u','a'),self.event),'rejected')
        self.s.close();self.s=Store(self.path)
        self.assertEqual(self.s.details(('u','a'),['e1']),[])

    def test_history(self):
        self.s.ingest(('u','a'),dict(self.event,id='old',text='用户以前喜欢Java',state='historical'))
        self.assertEqual(self.s.search(('u','a'),'Java',historical=False),[])
        self.assertEqual(self.s.search(('u','a'),'Java')[0]['id'],'old')

    def test_failure_is_not_evidence(self):
        class Broken:
            calls=[]
            def plan(self,*args): raise TimeoutError('offline')
        r=recall(self.s,('u','a'),'Python',Broken())
        self.assertEqual(r['status'],'partial')
        self.assertEqual(r['items'],[])
        self.assertTrue(r['fallback_candidates'])

    def test_sql_input(self):
        self.s.search(('u','a'),"' OR 1=1; DROP TABLE events;")
        self.assertTrue(self.s.details(('u','a'),['e1']))

    def test_model_cannot_select_unseen_id(self):
        model=LocalModel({})
        model.ask=lambda *args: {'ids':['other-user-id']}
        with self.assertRaises(ValueError):
            model.select('Python',self.s.details(('u','a'),['e1']),time.monotonic()+1)

    def test_empty_results_retry_bounded(self):
        class Empty:
            calls=[]
            def plan(self,*args): return {'topic':'','keywords':['不存在'],'historical':True}
            def select(self,*args): return []
        r=recall(self.s,('u','a'),'不存在',Empty(),max_rounds=100)
        self.assertEqual(len(r['trace']),3)
        self.assertEqual(r['status'],'not_found')

    def test_busy_database(self):
        other=Store(self.path)
        other.db.execute('BEGIN IMMEDIATE')
        try:
            import sqlite3
            with self.assertRaises(sqlite3.OperationalError):
                self.s.ingest(('u','a'),dict(self.event,id='new'))
        finally:
            other.db.rollback();other.close()

if __name__=='__main__': unittest.main()

