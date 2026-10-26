"""Local dedicated Edge visibility controls and independent recovery guard."""
import argparse
import json
import time
import os
from app import window_visibility as visibility


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['bind','hide','show','status','watch','test-recovery'])
    parser.add_argument('--pid',type=int)
    args=parser.parse_args()
    if args.action=='bind':result=visibility.bind(visibility.Native(),args.pid)
    elif args.action=='test-recovery':
        before=visibility.command('status')
        if not before['all_hidden']:raise ValueError('Hide the bound browser before testing recovery')
        visibility.guard_step(attention_override='recovery_self_test');time.sleep(.4)
        after=visibility.command('status')
        result={'self_test':'recovery_without_connection_or_login_changes','restored':not after['all_hidden'],
                'attention_latched':after['attention_latched'],'pid':after['pid']}
    elif args.action!='watch':result=visibility.command(args.action)
    else:
        import msvcrt
        lock=(visibility.ROOT/'window-guard.lock').open('a+b')
        if lock.tell()==0:lock.write(b'0');lock.flush()
        lock.seek(0)
        try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:return
        visibility.write(visibility.ROOT/'window-guard-process.json',{'pid':os.getpid(),'started_at':time.time()})
        try:
            while True:
                try:state=visibility.guard_step()
                except Exception as exc:state={'updated_at':time.time(),'error':str(exc)}
                visibility.write(visibility.ROOT/'window-guard-status.json',state)
                time.sleep(5)
        finally:lock.close()
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
