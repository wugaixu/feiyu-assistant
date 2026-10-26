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

if __name__=='__main__':unittest.main()
