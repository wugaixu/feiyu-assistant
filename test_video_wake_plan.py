import unittest
from datetime import datetime,timedelta
from unittest.mock import patch
from app import video_wake_plan as p
from app.video_rounds import TZ

class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.cfg={'windows':[['10:30','12:30']],'window_wakes':[2],'min_gap_minutes':10,'skip_chance':0.3}
        self.now=datetime(2026,10,24,10,30,tzinfo=TZ)
    def test_skip_consumes_one_opportunity_and_gap_is_hard(self):
        with patch.object(p.random.SystemRandom,'uniform',side_effect=lambda a,b:a),patch.object(p.random.SystemRandom,'random',return_value=0):
            first=p.plan(self.cfg,{},self.now)
            self.assertTrue(first['pending']['skip'])
            done=p.complete(first,self.now)
            second=p.plan(self.cfg,done,self.now+timedelta(seconds=1))
            self.assertEqual(second['pending']['ordinal'],2)
            self.assertGreaterEqual(second['pending']['at'],self.now.timestamp()+600)
    def test_pending_survives_restart_without_random_draw(self):
        initial=p.plan(self.cfg,{},self.now)
        with patch.object(p.random.SystemRandom,'uniform',side_effect=AssertionError):
            self.assertEqual(p.plan(self.cfg,initial,self.now),initial)
    def test_insufficient_time_does_not_compress_gap(self):
        state={'day':'2026-10-24','last_fired':datetime(2026,10,24,12,25,tzinfo=TZ).timestamp()}
        self.assertNotIn('pending',p.plan(self.cfg,state,datetime(2026,10,24,12,26,tzinfo=TZ)))
    def test_cross_midnight_uses_original_window_key(self):
        cfg=dict(self.cfg,windows=[['23:00','01:00']])
        plan=p.plan(cfg,{},datetime(2026,10,25,0,20,tzinfo=TZ))
        self.assertEqual(plan['pending']['key'],'2026-10-24|23:00–01:00')
