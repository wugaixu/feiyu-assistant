"""Local, structured video-round controls. No user text is executed as code."""
import argparse,json,sqlite3
from app import video_rounds as rounds

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['config','set-config','request','result','status','review','manual-test'])
    p.add_argument('--task');p.add_argument('--file');p.add_argument('--id')
    a=p.parse_args()
    with sqlite3.connect(rounds.ROOT/'assistant.sqlite3') as db:
        rounds.schema(db)
        if a.action=='config':result=rounds.config()
        elif a.action=='manual-test':result=rounds.authorize_three_tests(db,a.task)
        elif a.action=='set-config':
            rounds.require_task(db,a.task)
            result=rounds.save_config(json.loads(open(a.file,encoding='utf-8').read()))
        elif a.action=='request':result={'id':rounds.request(db,a.task,json.loads(open(a.file,encoding='utf-8').read()))}
        elif a.action=='review':result=rounds.review(db,a.task,json.loads(open(a.file,encoding='utf-8').read()))
        elif a.action=='result':
            row=db.execute('SELECT status,result_json FROM video_browser_commands WHERE id=?',(a.id,)).fetchone()
            result={'status':row[0],'result':json.loads(row[1]) if row[1] else None} if row else None
        else:result={'settings':rounds.config(),'rounds':db.execute('SELECT * FROM video_rounds ORDER BY created DESC LIMIT 10').fetchall()}
        print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
