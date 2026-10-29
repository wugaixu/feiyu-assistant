import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app import task_context
from app.event_dispatch import envelope

class PacketTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=task_context.PACKETS.parent)
        self.addCleanup(self.temp.cleanup)
        self.patch=patch.object(task_context,'PACKETS',Path(self.temp.name));self.patch.start();self.addCleanup(self.patch.stop)
    def test_compact_push_preserves_full_local_evidence(self):
        message={'kind':'text','raw_text':'长消息'*400}
        record={'kind':'video_card','status':'saved','files':[{'path':'local.mp4','bytes':5}],
            'video':{'video_id':'123','description':'作品标题'},
            'shared_video_analysis':{'contact_sheet':'sheet.jpg','subtitle_strip':'strip.jpg','frames':['one.jpg']},
            'comments':{'status':'done','items':[{'text':'观众观点','likes':2}]}}
        push=envelope('a'*32,'verified',json.dumps(message),[record])
        payload=json.loads(push.splitlines()[1])
        self.assertNotIn('received_media',payload);self.assertTrue(payload['text_truncated'])
        path=Path(payload['context_file']);text=path.read_text(encoding='utf-8')
        self.assertIn(message['raw_text'],text);self.assertIn('观众观点',text);self.assertIn('sheet.jpg',text)
        data=json.loads(path.with_name('data.json').read_text(encoding='utf-8'))
        self.assertEqual(data['received_media'],[record])
        self.assertNotIn('观众观点',push)
    def test_standalone_share_does_not_push_raw_card_json(self):
        message={'kind':'shared_video','raw_text':'huge raw JSON'*100,'sdk_content':{'share':{'title':'作品名'}}}
        push=envelope('b'*32,'verified',json.dumps(message),[])
        self.assertIn('作品名',push);self.assertNotIn('huge raw JSON',push)
    def test_media_summary_has_no_internal_instruction(self):
        value=task_context.summary({'kind':'shared_media','original_kind':'image','sdk_content':{'share':{}}})
        self.assertEqual(value,'[分享图片或表情]')
        self.assertNotIn('请读取本地资料',value)

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):task_context.packet_path('../escape')

    def test_sdk_media_payload_never_becomes_model_prose(self):
        secret=json.dumps({'aweType':2702,'inline_pic':'BASE64_SENTINEL','resource_url':{'skey':'SECRET_SENTINEL'}},ensure_ascii=False)
        raw={'kind':'text','merged_messages':[
            {'task_id':'a','kind':'image','text':secret,'received':100},
            {'task_id':'b','kind':'text','text':'把这张图画得更美一点','received':101}]}
        push=task_context.request('d'*32,'owner',raw,[])
        packet=task_context.packet_path('d'*32).read_text(encoding='utf-8')
        for text in (push,packet):
            self.assertIn('[分享图片或表情]',text)
            self.assertNotIn('BASE64_SENTINEL',text)
            self.assertNotIn('SECRET_SENTINEL',text)
            self.assertNotIn('aweType',text)
        # Raw evidence remains local and unmodified for diagnostics.
        diagnostic=json.loads(task_context.packet_path('d'*32).with_name('data.json').read_text(encoding='utf-8'))
        self.assertEqual(diagnostic['message']['merged_messages'][0]['text'],secret)
        self.assertEqual(task_context.summary({'kind':'text','raw_text':secret}),secret)

    def test_media_normalization_preserves_titles_and_voice_transcription(self):
        for kind in ('image','video','audio','video_card','file'):
            self.assertNotIn('inline_pic',task_context.summary({'kind':kind,'raw_text':'{"inline_pic":"opaque"}'}))
        self.assertEqual(task_context.summary({'kind':'shared_video','raw_text':'[分享抖音作品] 舞狮'}),'[分享抖音作品] 舞狮')
        text='[鲸聊音频自动转写，可能有误]\n你好'
        self.assertEqual(task_context.summary({'kind':'shared_media','original_kind':'audio','raw_text':text}),text)

    def test_group_owner_identity_comes_from_uid_not_nickname(self):
        from app.sdk_receiver import OWNER_UID
        raw={'is_group':True,'sender_uid':'other','sender_name':'主人','sender_is_owner':True}
        self.assertFalse(task_context.source_info(raw)['sender_is_owner'])
        raw['sender_uid']=OWNER_UID
        self.assertTrue(task_context.source_info(raw)['sender_is_owner'])

    def test_merged_packet_keeps_order_time_and_all_links(self):
        raw={'kind':'text','links':['https://example.com/a'], 'merged_messages':[
            {'task_id':'a','kind':'text','text':'先看图片','received':100},
            {'task_id':'b','kind':'shared_media','original_kind':'image','text':'[分享图片]','received':105},
            {'task_id':'c','kind':'text','text':'只看右边的','received':108}]}
        path=task_context.write_packet('c'*32,'owner',raw,[])
        text=path.read_text(encoding='utf-8')
        self.assertLess(text.index('先看图片'),text.index('只看右边的'))
        self.assertIn('08:01:45 | image',text)
        self.assertIn('https://example.com/a',text)

if __name__=='__main__':unittest.main()
