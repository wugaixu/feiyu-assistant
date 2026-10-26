import json,sqlite3,tempfile,unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from app import video_rounds as v
from app.live_service import LiveStore

class VideoRoundsTests(unittest.TestCase):
    def setUp(self):
        base=v.ROOT/'tests';base.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=base)
        self.root=Path(self.tmp.name)
        self.patch=patch.object(v,'ROOT',self.root);self.patch.start()
        self.store=LiveStore(self.root/'test.sqlite3');self.db=self.store.db
    def tearDown(self):
        self.db.close();self.patch.stop();self.tmp.cleanup()
    def test_cross_midnight_window_and_end_boundary(self):
        windows=[['22:00','02:00']]
        self.assertTrue(v.in_window(datetime(2026,10,23,23,0),windows))
        self.assertTrue(v.in_window(datetime(2026,10,24,1,59),windows))
        self.assertFalse(v.in_window(datetime(2026,10,24,2,0),windows))
    def test_invalid_settings_cannot_enable_or_overflow(self):
        for cfg in ({'enabled':True},{'windows':[['12:00','12:00']]},{'daily_videos':True},{'daily_comments':21},{'keywords':['']},{'extra':True}):
            with self.assertRaises(ValueError):v.validate_config(cfg)
    def test_recommendation_mode_does_not_require_keywords(self):
        cfg=v.validate_config({'enabled':True,'video_source':'recommendation','windows':[['12:00','13:00']]})
        self.assertEqual(cfg['keywords'],[])
        with self.assertRaises(ValueError):v.validate_config({'video_source':'unknown'})
    def test_restart_and_multiple_ticks_do_not_duplicate_daily_round(self):
        v.save_config({'enabled':True,'keywords':['AI'],'windows':[['12:00','13:00']]})
        now=datetime(2026,10,23,12,30,tzinfo=v.TZ)
        v.tick(self.db,'owner',now);v.tick(self.db,'owner',now)
        self.assertEqual(self.db.execute('SELECT count(*) FROM video_rounds').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM live_tasks').fetchone()[0],1)
        v.tick(self.db,'owner',datetime(2026,10,24,12,30,tzinfo=v.TZ))
        self.assertEqual(self.db.execute('SELECT count(*) FROM video_rounds').fetchone()[0],2)
    def test_disabled_and_outside_window_stay_quiet(self):
        v.tick(self.db,'owner')
        self.assertEqual(self.db.execute('SELECT count(*) FROM live_tasks').fetchone()[0],0)
    def test_random_time_is_durable_and_only_fires_once(self):
        v.save_config({'enabled':True,'keywords':['AI'],'windows':[['12:00','13:00']],'random_start':True})
        now=datetime(2026,10,23,12,0,tzinfo=v.TZ)
        target=datetime(2026,10,23,12,20,tzinfo=v.TZ)
        with patch.object(v.random.SystemRandom,'uniform',return_value=target.timestamp()) as choose:
            v.tick(self.db,'owner',now)
            v.tick(self.db,'owner',now)
            self.assertEqual(choose.call_count,1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM live_tasks').fetchone()[0],0)
        self.store.db.close();self.store=LiveStore(self.root/'test.sqlite3');self.db=self.store.db
        v.tick(self.db,'owner',target);v.tick(self.db,'owner',target)
        self.assertEqual(self.db.execute('SELECT count(*) FROM live_tasks').fetchone()[0],1)
    def test_multiple_wakes_keep_total_window_quota(self):
        cfg=v.save_config({'enabled':True,'keywords':['AI'],'windows':[['12:00','13:00']],
            'window_limits':[6],'daily_videos':6,'random_start':True,'window_wakes':[2],'skip_chance':0})
        now=datetime(2026,10,24,12,0,tzinfo=v.TZ)
        with patch.object(v.random.SystemRandom,'uniform',side_effect=lambda a,b:a):
            v.tick(self.db,'owner',now)
            first=self.db.execute('SELECT id FROM video_rounds').fetchone()[0]
            with self.db:self.db.execute("UPDATE live_tasks SET status='processing' WHERE id=?",(first,))
            for i in range(5):v.reserve_video(self.db,first,str(7100000000000000000+i),cfg,now)
            with self.assertRaisesRegex(ValueError,'Per-wake'):v.reserve_video(self.db,first,'7100000000000000099',cfg,now)
            with self.db:self.db.execute("UPDATE live_tasks SET status='done' WHERE id=?",(first,))
            later=datetime(2026,10,24,12,10,tzinfo=v.TZ)
            v.tick(self.db,'owner',later)
            second=self.db.execute('SELECT id FROM video_rounds WHERE wake=2').fetchone()[0]
            raw=json.loads(self.db.execute('SELECT raw_json FROM live_tasks WHERE id=?',(second,)).fetchone()[0])
            self.assertEqual(raw['max_videos'],1)
    def test_late_enabling_does_not_catch_up_or_schedule_past_window(self):
        v.schema(self.db)
        now=datetime(2026,10,23,12,55,tzinfo=v.TZ)
        self.assertIsNone(v.random_start(self.db,now,0,[['12:00','13:00']]))
        start=datetime(2026,10,23,12,0,tzinfo=v.TZ)
        chosen=v.random_start(self.db,start,0,[['12:00','13:00']])
        self.assertGreaterEqual(chosen,start.timestamp())
        self.assertLessEqual(chosen,datetime(2026,10,23,12,45,tzinfo=v.TZ).timestamp())
        v.save_config({'enabled':True,'keywords':['AI'],'windows':[['12:00','13:00']]})
        v.tick(self.db,'owner',datetime(2026,10,23,11,30,tzinfo=v.TZ))
        self.assertEqual(self.db.execute('SELECT count(*) FROM live_tasks').fetchone()[0],0)
    def test_unclaimed_job_cannot_issue_browser_commands(self):
        with self.assertRaises(ValueError):v.request(self.db,'a'*32,{'action':'comment'})
    def test_daily_quota_and_duplicate_inspection_are_durable(self):
        v.schema(self.db)
        task='b'*32
        with self.db:self.db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,?)',(task,'owner',1,'{}','processing',None))
        cfg=v.validate_config({'daily_videos':1})
        v.reserve_video(self.db,task,'7699447833535246307',cfg)
        with self.assertRaises(ValueError):v.reserve_video(self.db,task,'7699447833535246307',cfg)
        with self.assertRaises(ValueError):v.reserve_video(self.db,task,'7699447833535246308',cfg)
        self.assertEqual(self.db.execute('SELECT count(*) FROM video_reviews').fetchone()[0],1)
        v.review(self.db,task,{'video_id':'7699447833535246307','verdict':'skip','reason':'No reliable content','evidence':'visible_text'})
        with self.assertRaises(ValueError):v.review(self.db,task,{'video_id':'7699447833535246307','verdict':'interesting','reason':'Changed mind','evidence':'video_frames'})
    def test_owner_window_quotas_do_not_borrow_from_other_slots(self):
        cfg={'enabled':True,'keywords':['DSH','Codex'],'windows':[['10:30','12:30'],['13:30','14:00'],['17:00','21:00']],
             'window_limits':[6,3,8],'daily_videos':17,'daily_comments':5}
        cfg=v.save_config(cfg)
        first=datetime(2026,10,23,11,0,tzinfo=v.TZ)
        v.tick(self.db,'owner',first)
        task=self.db.execute('SELECT id FROM video_rounds WHERE slot=0').fetchone()[0]
        with self.db:self.db.execute("UPDATE live_tasks SET status='processing' WHERE id=?",(task,))
        for i in range(6):v.reserve_video(self.db,task,str(7000000000000000000+i),cfg,first)
        with self.assertRaises(ValueError):v.reserve_video(self.db,task,'7000000000000000099',cfg,first)
        second=datetime(2026,10,23,13,40,tzinfo=v.TZ)
        v.tick(self.db,'owner',second)
        self.assertEqual(self.db.execute('SELECT count(*) FROM video_rounds').fetchone()[0],2)
        with self.assertRaises(ValueError):v.reserve_video(self.db,task,'7000000000000000099',cfg,second)
    def test_five_comment_attempts_cap_and_no_repeat_after_uncertain_result(self):
        cfg=v.save_config({'enabled':True,'auto_comment':True,'keywords':['DSH'],'windows':[['10:30','12:30']],
                           'window_limits':[6],'daily_videos':6,'daily_comments':5})
        now=datetime(2026,10,23,11,0,tzinfo=v.TZ)
        v.tick(self.db,'owner',now)
        task=self.db.execute('SELECT id FROM video_rounds').fetchone()[0]
        with self.db:self.db.execute("UPDATE live_tasks SET status='processing'")
        for i in range(6):
            vid=str(7000000000000000000+i)
            v.reserve_video(self.db,task,vid,cfg,now)
            v.review(self.db,task,{'video_id':vid,'verdict':'interesting','reason':'Evidence obtained','evidence':'video_frames'})
        for i in range(5):v.reserve_comment(self.db,task,str(7000000000000000000+i),'有趣的演示',cfg,now)
        with self.assertRaises(ValueError):v.reserve_comment(self.db,task,'7000000000000000005','第六条',cfg,now)
        with self.assertRaises(ValueError):v.reserve_comment(self.db,task,'7000000000000000000','不能重发',cfg,now)
        with self.assertRaises(ValueError):v.reserve_comment(self.db,task,'7000000000000000005','窗外',cfg,datetime(2026,10,23,13,0,tzinfo=v.TZ))
        with self.db:
            self.db.execute('INSERT INTO comment_quota_resets VALUES(?,?,?)',('2026-10-23',self.db.execute('SELECT max(rowid) FROM video_comments').fetchone()[0],now.timestamp()))
        v.reserve_comment(self.db,task,'7000000000000000005','重置后新评论',cfg,now)
        self.assertEqual(self.db.execute('SELECT count(*) FROM video_comments').fetchone()[0],6)
        with self.assertRaises(ValueError):v.reserve_comment(self.db,task,'7000000000000000000','仍不可重复',cfg,now)
    def test_entertainment_filter_blocks_unclassified_review(self):
        v.schema(self.db);task='d'*32
        with self.db:
            self.db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,?)',(task,'owner',1,json.dumps({'kind':'video_round'}),'processing',None))
            self.db.execute('INSERT INTO video_reviews VALUES(?,?,?,?,?)',('7100000000000000000',task,'2026-10-23','inspecting','{}'))
        v.save_config({'recommend_categories':['entertainment','comedy']})
        review={'video_id':'7100000000000000000','verdict':'interesting','reason':'实际画面有笑点','evidence':'video_frames'}
        with self.assertRaises(ValueError):v.review(self.db,task,review)
        review.update(category='comedy',category_reason='人物对话的反转来自已观察的字幕和画面')
        self.assertEqual(v.review(self.db,task,review)['status'],'interesting')
    def test_owner_identity_requires_exact_profile_not_duplicate_name(self):
        self.assertIsNone(v.owner_identity_in_response({'sec_uid':'someone_else','nickname':'搞点饭吃吃'}))
        owner={'sec_uid':v.OWNER_PATH.rsplit('/',1)[-1],'nickname':'搞点饭吃吃','avatar_thumb':{'uri':'known-avatar'}}
        self.assertEqual(v.owner_identity_in_response({'users':[owner]})['avatar_uri'],'known-avatar')
    def test_manual_three_tests_need_explicit_message_and_stay_bounded(self):
        v.schema(self.db);task='c'*32
        with self.db:self.db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,?)',(task,'owner',1,json.dumps({'raw_text':'现在进行3次刷视频测试'},ensure_ascii=False),'processing',None))
        cfg=v.validate_config({'enabled':True,'keywords':['DSH'],'windows':[['10:30','12:30']],'daily_videos':17})
        now=datetime(2026,10,23,3,0,tzinfo=v.TZ)
        with self.assertRaises(ValueError):v.reserve_video(self.db,task,'7000000000000000000',cfg,now)
        v.authorize_three_tests(self.db,task)
        for i in range(3):v.reserve_video(self.db,task,str(7000000000000000000+i),cfg,now)
        with self.assertRaises(ValueError):v.reserve_video(self.db,task,'7000000000000000003',cfg,now)
        with self.db:self.db.execute('UPDATE video_manual_tests SET expires=0')
        self.assertIsNone(v.manual_test(self.db,task))

if __name__=='__main__':unittest.main()
