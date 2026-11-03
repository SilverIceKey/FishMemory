"""Broad recall + original batch selector. Dataset is now development data."""
import hashlib
import json
import statistics
import sys
import tempfile
from pathlib import Path
from fishmemory.core import Store,LocalModel
from fishmemory.retrieval_v2 import recall_v2
from .run import metrics


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    raw=Path('benchmarks/heldout.json').read_bytes(); data=json.loads(raw)
    model=LocalModel(json.loads(Path('config.example.json').read_text()))
    rows=[]
    with tempfile.TemporaryDirectory() as td:
        store=Store(Path(td)/'ablation.sqlite')
        try:
            for i,(ident,topic,text,state) in enumerate(data['events']):
                store.ingest(('u','a'),dict(id=ident,topic=topic,text=text,state=state,time=f'2026-09-{i+1:02d}T00:00:00Z'))
            for name,query,gold,_ in data['cases']:
                result=recall_v2(store,('u','a'),query,model,selection='batch')
                score=metrics([c['id'] for c in result['items']],gold)
                rows.append(dict(case=name,query=query,gold=gold,result=result,metrics=score))
                print(name,score['exact'],flush=True)
        finally:store.close()
    summary={'exact':sum(r['metrics']['exact'] and not r['result'].get('error') for r in rows),
             'positive_covered':sum(set(r['gold'])<=set(r['result']['candidate_ids']) for r in rows if r['gold']),
             'calls':len(model.calls),'input_tokens':sum(c['usage'].get('prompt_tokens',0) for c in model.calls),
             'output_tokens':sum(c['usage'].get('completion_tokens',0) for c in model.calls),
             'median_ms':statistics.median(r['result']['elapsed_ms'] for r in rows)}
    Path('reports/retrieval-broad-batch.json').write_text(json.dumps(dict(summary=summary,rows=rows,dataset_sha256=hashlib.sha256(raw).hexdigest(),online_calls=0),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))

if __name__=='__main__': main()

