import json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from app import whale_channel as channel
from app.live_service import ROOT
class PublicHealthTests(unittest.TestCase):
    def test_fresh_failure_does_not_become_local_success_and_stale_is_unknown(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder,patch.object(channel,'ROOT',Path(folder)):
            p=Path(folder)/'whale-data/public-health.json';p.parent.mkdir()
            p.write_text(json.dumps({'reachable':False,'state':'tls_unreachable','checked_at':time.time()}),encoding='utf-8')
            self.assertIs(channel.public_health()['reachable'],False)
            p.write_text(json.dumps({'reachable':True,'state':'reachable','checked_at':time.time()-181}),encoding='utf-8')
            self.assertIsNone(channel.public_health()['reachable'])
            p.write_text('bad',encoding='utf-8');self.assertIsNone(channel.public_health()['reachable'])
if __name__=='__main__':unittest.main()
