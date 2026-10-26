"""Durable multi-wake scheduling adapted from dsh-bili-whale's MIT planner.

Reference: Alterssss/dsh-bili-whale, tools/lib/night-plan.mjs,
ef3f74171b4104249f5583c4d2df9bebad14934a. Uses Beijing time explicitly.
"""
from datetime import datetime,timedelta
import random
import json


def plan(cfg,state,now):
    state=dict(state)
    schedule=json.dumps({k:cfg[k] for k in ('windows','window_wakes','min_gap_minutes','skip_chance')},sort_keys=True)
    if state.get('schedule')!=schedule:state.pop('pending',None)
    state['schedule']=schedule
    used=dict(state.get('used',{}))
    state['used']=used
    pending=state.get('pending')
    if pending and pending['end']>now.timestamp():return state
    state.pop('pending',None)
    today=now.strftime('%Y-%m-%d')
    if state.get('day')!=today:state.update(day=today,wakes=0)
    limit=min(12,sum(cfg['window_wakes']))
    if state.get('wakes',0)>=limit:return state
    pool=[]
    for slot,(a,b) in enumerate(cfg['windows']):
        for delta in (-1,0):
            date=now.date()+timedelta(days=delta)
            start=datetime.combine(date,datetime.strptime(a,'%H:%M').time(),now.tzinfo)
            end=datetime.combine(date,datetime.strptime(b,'%H:%M').time(),now.tzinfo)
            if end<=start:end+=timedelta(days=1)
            key=start.strftime('%Y-%m-%d')+'|'+a+'–'+b
            if end.timestamp()<=now.timestamp() or used.get(key,0)>=cfg['window_wakes'][slot]:continue
            pool.append((end.timestamp(),start.timestamp(),slot,key))
    for end,start,slot,key in sorted(pool):
        earliest=max(now.timestamp(),start,state.get('last_fired',0)+cfg['min_gap_minutes']*60)
        if earliest>=end-30:continue
        rng=random.SystemRandom()
        state['pending']={'key':key,'slot':slot,'at':rng.uniform(earliest,end),
                          'start':start,'end':end,'skip':rng.random()<cfg['skip_chance'],
                          'ordinal':used.get(key,0)+1}
        break
    return state


def complete(state,now):
    state=dict(state);pending=state.pop('pending')
    state['used']=dict(state['used'])
    state['used'][pending['key']]=state['used'].get(pending['key'],0)+1
    state['wakes']=state.get('wakes',0)+1
    state['last_fired']=now.timestamp()
    return state
