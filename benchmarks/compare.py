"""Run both methods on frozen new questions; preserve original benchmark output."""
import hashlib
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from fishmemory.core import Store,LocalModel,recall
from fishmemory.retrieval_v2 import recall_v2,select_each
from .run import metrics


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    raw=Path('benchmarks/heldout.json').read_bytes(); data=json.loads(raw)
    model=LocalModel(json.loads(Path('config.example.json').read_text()))
    rows=[]
    with tempfile.TemporaryDirectory() as td:
        store=Store(Path(td)/'compare.sqlite')
        try:
            for i,(ident,topic,text,state) in enumerate(data['events']):
                store.ingest(('u','a'),dict(id=ident,topic=topic,text=text,state=state,time=f'2026-09-{i+1:02d}T00:00:00Z'))
            for index,(name,query,gold,oracle_ids) in enumerate(data['cases']):
                row=dict(case=name,query=query,gold=gold)
                # Alternate order to reduce consistently favoring one cache condition.
                methods=[('v1',recall),('v2',recall_v2)]
                for version,fn in methods[::1 if index%2==0 else -1]:
                    result=fn(store,('u','a'),query,model)
                    row[version]={'result':result,'metrics':metrics([x['id'] for x in result['items']],gold)}
                candidates=store.details(('u','a'),oracle_ids)
                for version in ('v1','v2'):
                    try:
                        if version=='v1': ids=model.select(query,candidates,time.monotonic()+30)
                        else: ids,_=select_each(model,query,candidates,time.monotonic()+30)
                        row[version]['oracle']={'ids':ids,**metrics(ids,gold)}
                    except Exception as error:
                        row[version]['oracle']={'exact':False,'error':type(error).__name__}
                rows.append(row)
                print(name,[(v,row[v]['metrics']['exact'],row[v]['oracle']['exact']) for v in ('v1','v2')],flush=True)
        finally: store.close()
    summary={}
    for v in ('v1','v2'):
        calls=[c for r in rows for c in r[v]['result']['calls']]
        summary[v]={'exact':sum(r[v]['metrics']['exact'] and not r[v]['result'].get('error') for r in rows),
          'oracle_exact':sum(r[v]['oracle']['exact'] for r in rows),
          'positive_covered':sum(set(r['gold'])<=set(r[v]['result'].get('candidate_ids',[])) for r in rows if r['gold']),
          'end_to_end_calls':len(calls),'input_tokens':sum(c['usage'].get('prompt_tokens',0) for c in calls),
          'output_tokens':sum(c['usage'].get('completion_tokens',0) for c in calls),
          'median_ms':statistics.median(r[v]['result']['elapsed_ms'] for r in rows)}
    result={'dataset_sha256':hashlib.sha256(raw).hexdigest(),'cases':len(rows),'positive_cases':sum(bool(r['gold']) for r in rows),'summary':summary,'rows':rows,'online_calls':0,'all_local_calls':len(model.calls)}
    Path('reports/retrieval-v2-comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))

if __name__=='__main__': main()

