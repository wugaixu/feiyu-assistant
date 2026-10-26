import json,unittest,time
from unittest.mock import patch
from app.live_service import LiveStore
from app import shared_comments as c

VIDEO='7697598283775355313'
CFG={'enabled':True,'enabled_since':1,'daily_limit':10}


class FakeBrowser:
    """Stand-in for HiddenCommentBrowser with the same public surface."""

    def __init__(self):
        self.published=[]
        self.closed=False
        self.self={'status_code':0,'user_uid':c.ACCOUNT_UID,'nickname':'饭团小助理'}
        self.owner={'uid':c.OWNER_UID,'sec_uid':c.OWNER_SEC_UID,'nickname':'__OWNER_NICKNAME__'}
        self.receipt_overrides={}
        self.publish_error=None

    def self_identity(self):
        return self.self

    def publish(self,video_id,text,mention_owner=True,kind='video'):
        if self.publish_error:raise self.publish_error
        self.published.append((video_id,text))
        self.last_mention_owner=mention_owner
        if mention_owner:
            full=text+' @__OWNER_NICKNAME__'
            comment={'cid':'1234567890','text':full,'text_extra':[{'user_id':c.OWNER_UID}],'user':{'uid':c.ACCOUNT_UID}}
        else:
            full=text
            comment={'cid':'1234567890','text':full,'text_extra':[],'user':{'uid':c.ACCOUNT_UID}}
        comment.update(self.receipt_overrides.pop('comment',{}))
        receipt={'status_code':0,'comment':comment,'editor_text':full}
        if mention_owner:receipt['owner_identity']=dict(self.owner)
        receipt.update(self.receipt_overrides.pop('receipt',{}))
        return receipt

    def comments(self,video_id,kind='video'):
        mention=getattr(self,'last_mention_owner',True)
        if mention:
            comment={'cid':'1234567890','text':'这个反差挺有意思。 @__OWNER_NICKNAME__',
                'text_extra':[{'user_id':c.OWNER_UID}],'user':{'uid':c.ACCOUNT_UID}}
        else:
            comment={'cid':'1234567890','text':'这个反差挺有意思。',
                'text_extra':[],'user':{'uid':c.ACCOUNT_UID}}
        return self.receipt_overrides.pop('list',{'status_code':0,'comments':[comment]})

    def close(self):
        self.closed=True


class SharedCommentTests(unittest.TestCase):
    def setUp(self):
        self.db=LiveStore(':memory:').db;self.addCleanup(self.db.close)
        self.patch=patch.object(c,'policy',return_value=CFG);self.patch.start();self.addCleanup(self.patch.stop)
        self.message={'transport':'sdk','association_received_at':time.time()}
        self.record={'source':'sdk','kind':'video_card','received_at':time.time(),'status':'saved',
            'video':{'video_id':VIDEO,'source':'sdk_shared_work'},'files':[{'path':'saved.mp4'}],
            'shared_video_analysis':{'status':'done'}}
        self.db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',('task',c.OWNER,time.time(),json.dumps(self.message),'processing'))
        self.save_record();self.db.commit()

    def save_record(self):
        self.db.execute('INSERT OR REPLACE INTO media_records VALUES(?,?,?,?,?,?)',('media',c.OWNER,time.time(),'video_card',json.dumps(self.record),'task'));self.db.commit()

    def queue(self,text='这个反差挺有意思。'):
        return c.enqueue(self.db,'task',VIDEO,{'text':text,'evidence':'contact-sheet.jpg：画面中有反差情节。'})

    def test_queue_and_publish_readback_once(self):
        self.queue();api=FakeBrowser();result=c.step(self.db,api)
        self.assertEqual(result['status'],'visible_in_account_history')
        self.assertFalse(result['owner_notification_confirmed'])
        self.assertEqual(result['transport'],'real_browser')
        self.assertIsNone(c.step(self.db,api));self.assertEqual(len(api.published),1)
        with self.assertRaises(Exception):self.queue()

    def test_mention_owner_off_publishes_plain_comment(self):
        self.queue();api=FakeBrowser()
        with patch.object(c,'policy',return_value={**CFG,'mention_owner':False}):
            result=c.step(self.db,api)
        self.assertEqual(result['status'],'visible_in_account_history')
        self.assertFalse(result['mention_owner'])
        self.assertFalse(result['mention_in_readback'])
        self.assertEqual(api.published,[(VIDEO,'这个反差挺有意思。')])

    def test_wrong_account_blocks_write(self):
        self.queue();api=FakeBrowser();api.self={'status_code':0,'user_uid':'111','nickname':'x'}
        self.assertEqual(c.step(self.db,api)['status'],'blocked_before_publish');self.assertEqual(api.published,[])

    def test_wrong_owner_mention_is_uncertain(self):
        self.queue();api=FakeBrowser();api.owner={'uid':'999','sec_uid':'bad','nickname':'x'}
        result=c.step(self.db,api)
        self.assertEqual(result['status'],'outcome_uncertain');self.assertEqual(len(api.published),1)

    def test_timeout_never_repeats(self):
        self.queue();api=FakeBrowser();api.publish_error=TimeoutError()
        self.assertEqual(c.step(self.db,api)['status'],'outcome_uncertain');c.step(self.db,api)
        self.assertEqual(len(api.published),0)

    def test_persisted_attempt_survives_interruption(self):
        self.queue();self.db.execute("UPDATE shared_comment_outbox SET status='attempted'");self.db.commit()
        api=FakeBrowser();self.assertIsNone(c.step(self.db,api));self.assertEqual(api.published,[])

    def test_missing_mention_is_not_verified(self):
        self.queue();api=FakeBrowser()
        api.receipt_overrides['comment']={'text_extra':[]}
        api.receipt_overrides['list']={'status_code':0,'comments':[{'cid':'1234567890',
            'text':'这个反差挺有意思。 @__OWNER_NICKNAME__','text_extra':[],'user':{'uid':c.ACCOUNT_UID}}]}
        result=c.step(self.db,api)
        self.assertEqual(result['status'],'accepted_unverified');self.assertFalse(result['mention_in_readback'])

    def test_rejection_never_repeats(self):
        self.queue();api=FakeBrowser();api.receipt_overrides['receipt']={'status_code':8}
        self.assertEqual(c.step(self.db,api)['status'],'platform_rejected');c.step(self.db,api)
        self.assertEqual(len(api.published),1)

    def test_text_readback_recovers_missing_response(self):
        self.queue();api=FakeBrowser()
        api.receipt_overrides['receipt']={'status_code':None,'comment':None}
        result=c.step(self.db,api)
        self.assertEqual(result['status'],'visible_in_account_history')
        self.assertEqual(result['comment_id'],'1234567890')

    def test_unclaimed_task_rejected(self):
        self.db.execute("UPDATE live_tasks SET status='done'");self.db.commit()
        with self.assertRaises(ValueError):self.queue()

    def test_no_visual_evidence_rejected(self):
        self.record['shared_video_analysis']['status']='unavailable';self.save_record()
        with self.assertRaises(ValueError):self.queue()

    def test_gallery_without_frames_allowed(self):
        self.record['media_type']='image_gallery';self.record['shared_video_analysis']={'status':'not_applicable'};self.save_record()
        self.assertEqual(self.queue()['status'],'pending')

    def test_old_media_and_direct_upload_rejected(self):
        for change in ({'received_at':0},{'kind':'video'},{'source':'web'}):
            old=dict(self.record);self.record.update(change);self.save_record()
            with self.assertRaises(ValueError):self.queue()
            self.record=old

    def test_daily_quota_and_disabled(self):
        with patch.object(c,'policy',return_value={**CFG,'daily_limit':0}):
            with self.assertRaises(ValueError):self.queue()
        with patch.object(c,'policy',return_value={'enabled':False}):
            with self.assertRaises(ValueError):self.queue()

    def test_publish_rechecks_quota_after_queue(self):
        self.queue();api=FakeBrowser()
        with patch.object(c,'policy',return_value={**CFG,'daily_limit':0}):
            self.assertEqual(c.step(self.db,api)['status'],'quota_blocked')
        self.assertEqual(api.published,[])

    def test_manual_at_and_missing_review_rejected(self):
        with self.assertRaises(ValueError):self.queue('好看 @某人')
        with self.assertRaises(ValueError):c.enqueue(self.db,'task',VIDEO,{'text':'好看','evidence':''})

    def test_foreign_work_rejected(self):
        with self.assertRaises(ValueError):c.enqueue(self.db,'task','7697598283775355314',{'text':'好看','evidence':'真实画面依据路径'})

    def test_old_browser_attempt_prevents_duplicate(self):
        self.db.execute('CREATE TABLE video_comments(video_id TEXT)');self.db.execute('INSERT INTO video_comments VALUES(?)',(VIDEO,));self.db.commit()
        with self.assertRaises(ValueError):self.queue()

    def test_owned_browser_is_closed(self):
        self.queue()
        created={}
        class Manager(FakeBrowser):
            def __enter__(self):created['api']=self;return self
        fake=Manager()
        with patch('app.browser_publish.HiddenCommentBrowser',return_value=fake),patch('app.comment_profile.ProfileLease'):
            c.step(self.db)
        self.assertTrue(fake.closed)

    def test_login_holds_profile_without_reserving_comment_attempt(self):
        from app.comment_profile import ProfileBusy
        self.queue()
        with patch('app.comment_profile.ProfileLease.__enter__',side_effect=ProfileBusy('login in progress')):
            result=c.step(self.db)
        self.assertEqual(result['status'],'browser_busy')
        self.assertEqual(self.db.execute('SELECT status FROM shared_comment_outbox').fetchone()[0],'pending')

    def test_known_pre_submit_timeout_is_not_claimed_as_possible_submission(self):
        self.queue()
        fake=FakeBrowser()
        fake.last_diagnostic={'phase':'open_comment_editor','submitted':False}
        with patch.object(fake,'publish',side_effect=TimeoutError()):result=c.step(self.db,api=fake)
        self.assertEqual(result['status'],'blocked_before_publish')
        self.assertEqual(result['diagnostic']['phase'],'open_comment_editor')

    def test_explicit_manual_attempts_also_count_toward_daily_limit(self):
        self.queue()
        self.db.execute('CREATE TABLE manual_comment_attempts(day TEXT)')
        day=c.datetime.now(c.TZ).date().isoformat()
        self.db.execute('INSERT INTO manual_comment_attempts VALUES(?)',(day,));self.db.commit()
        api=FakeBrowser()
        with patch.object(c,'policy',return_value={**CFG,'daily_limit':1}):
            self.assertEqual(c.step(self.db,api)['status'],'quota_blocked')
        self.assertFalse(api.published)

    def test_platform_trimming_trailing_space_does_not_fail_text_check(self):
        receipt={'status_code':0,'comment':{'cid':'123','text':'评论 @主人','text_extra':[]}}
        self.assertTrue(c.check_receipt(receipt,'评论 @主人 ')['text_matches'])
        self.assertFalse(c.check_receipt(receipt,'另一条评论 @主人 ')['text_matches'])

    def test_browser_work_url_helpers_refuse_other_videos(self):
        from app import browser_publish as bp
        self.assertEqual(bp.target_paths('123','video')[0],'/video/123')
        self.assertEqual(bp.target_paths('123','image_gallery')[0],'/note/123')
        self.assertTrue(bp.page_on_target('https://www.douyin.com/video/123?foo=1','123'))
        self.assertTrue(bp.page_on_target('https://www.douyin.com/note/123/','123'))
        self.assertFalse(bp.page_on_target('https://www.douyin.com/video/999','123'))
        self.assertFalse(bp.page_on_target('https://www.douyin.com/','123'))


    def test_completed_reply_comment_requires_committed_astrbot_provenance(self):
        self.db.execute("UPDATE live_tasks SET status='done' WHERE id='task'");self.db.commit()
        with self.assertRaises(ValueError):self.queue()
        self.db.execute('CREATE TABLE chat_dispatch(task_id TEXT,status TEXT,detail TEXT)')
        self.db.execute("INSERT INTO reply_outbox(id,task_id,owner,text) VALUES('reply','task',?,'回复')",(c.OWNER,))
        self.db.execute('INSERT INTO chat_dispatch VALUES(?,?,?)',('task','processed',json.dumps({'provider':'astrbot'})));self.db.commit()
        result=c.enqueue(self.db,'task',VIDEO,{'text':'这个反差挺有意思。','evidence':'contact-sheet.jpg：真实画面依据'},after_reply=True)
        self.assertEqual(result['status'],'pending')
        with self.assertRaises(ValueError):c.enqueue(self.db,'task',VIDEO,{'text':'不重复','evidence':'真实画面依据路径'},after_reply=True)

if __name__=='__main__':unittest.main()
