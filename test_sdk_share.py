import copy,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from app.sdk_share import normalize
from app.sdk_receiver import ingest,OWNER_UID,ACCOUNT_UID,OWNER
from app.live_service import LiveStore,ROOT
from app.sdk_media import capture
from app.media_pipeline import dispatch_standalone_shared

WORK='7695415840661035365'
def card():
    return {'kind':'unknown','aweType':11054,'value':{
        'aweme_info':{'item_id':WORK,'author_uid':'75608911024'},'item_id':WORK,
        'description':'奥赛级微笑','schema':'aweme://aweme/detail/'+WORK+'?enter_from=chat',
        'im_dynamic_patch':{'card_type':'life_msg_video_poi','scene_type':'life',
            'raw_data':'untrusted card actions must never execute'}}}

class ShareTests(unittest.TestCase):
    def test_poi_normalizes_without_mutation(self):
        content=card();original=copy.deepcopy(content);result=normalize(content)
        self.assertEqual(result['kind'],'share');self.assertEqual(result['share']['itemId'],WORK)
        self.assertEqual(result['share']['authorUid'],'75608911024');self.assertEqual(content,original)
        self.assertEqual(result['value'],original['value']);self.assertEqual(normalize(result),result)

    def test_canonical_share_unchanged(self):
        content={'kind':'share','share':{'itemId':WORK}};self.assertEqual(normalize(content),content)

    def test_no_work_poi_and_ambiguous_cards_stay_unknown(self):
        variants=[]
        missing=card();del missing['value']['aweme_info'];variants.append(missing)
        mismatch=card();mismatch['value']['item_id']='7695415840661035366';variants.append(mismatch)
        invalid=card();invalid['value']['aweme_info']['item_id']='../../test';variants.append(invalid)
        unsupported=card();unsupported['value']['im_dynamic_patch']['card_type']='poi';unsupported['value']['schema']='aweme://poi/detail?id='+WORK;variants.append(unsupported)
        for content in variants:self.assertEqual(normalize(content)['kind'],'unknown')

    def test_unknown_explicit_work_schema_supported(self):
        content=card();content['value']['im_dynamic_patch']={}
        self.assertEqual(normalize(content)['kind'],'share')

    def test_ingest_routes_poi_to_media_and_deduplicates(self):
        store=LiveStore(':memory:');self.addCleanup(store.db.close)
        event={'seq':1,'message_id':'wire-poi','sender_uid':OWNER_UID,'account_uid':ACCOUNT_UID,'timestamp':time.time(),'text':'','content':card()}
        self.assertEqual(ingest(store.db,[event]),1);self.assertEqual(ingest(store.db,[event]),0)
        raw,state=store.db.execute('SELECT raw_json,status FROM live_tasks').fetchone();raw=json.loads(raw)
        self.assertEqual(state,'media_pending');self.assertEqual(raw['kind'],'video_card')
        self.assertEqual(raw['sdk_content']['share']['itemId'],WORK)
        self.assertEqual(raw['association_policy'],'media_10s_v1')

    def test_poi_capture_uses_work_download_not_cover(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            result={'status':'saved','path':'verified.mp4','decode_verified':True}
            with patch('app.sdk_media.ROOT',Path(directory)),patch('app.shared_video.download',return_value=result) as download:
                record=capture(None,'sample',{'kind':'video_card','sdk_content':card()})
            self.assertEqual(record['status'],'saved');self.assertEqual(record['kind'],'video_card')
            self.assertEqual(record['video']['source'],'sdk_shared_work');self.assertEqual(record['video']['video_id'],WORK)
            self.assertEqual(download.call_args.args[1]['video_id'],WORK)

    def test_poi_gets_ten_second_window_and_following_text_priority(self):
        store=LiveStore(':memory:');self.addCleanup(store.db.close);db=store.db
        event={'seq':1,'message_id':'poi','sender_uid':OWNER_UID,'account_uid':ACCOUNT_UID,'timestamp':time.time(),'content':card()}
        ingest(db,[event]);task,raw=db.execute('SELECT id,raw_json FROM live_tasks').fetchone();message=json.loads(raw)
        with db:
            db.execute("UPDATE live_tasks SET status='resource_saved'")
            db.execute('INSERT INTO media_records VALUES(?,?,?,?,?,NULL)',(task,OWNER,time.time(),'video_card',json.dumps({'kind':'video_card','shared_video_analysis':{'status':'done'}})))
        with patch('app.media_pipeline.time.time',return_value=message['association_received_at']+9):dispatch_standalone_shared(db,OWNER)
        self.assertEqual(db.execute('SELECT status FROM live_tasks').fetchone()[0],'resource_saved')
        with db:
            db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,NULL)',('text',OWNER,time.time()+1,json.dumps({'kind':'text','raw_text':'好身材'}),'pending'))
        with patch('app.media_pipeline.time.time',return_value=message['association_received_at']+11):dispatch_standalone_shared(db,OWNER)
        self.assertEqual(db.execute('SELECT status FROM live_tasks WHERE id=?',(task,)).fetchone()[0],'resource_saved')

if __name__=='__main__':unittest.main()
