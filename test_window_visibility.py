import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from app import window_visibility as guard


class FakeNative:
    def __init__(self):
        self.binding={'pid':42,'created_filetime':123,'executable':r'C:\msedge.exe'}
        self.visible=True;self.calls=[]
    def identity(self,pid):return self.binding
    def windows(self,pid):return [{'hwnd':111,'visible':self.visible}]
    def show(self,hwnd,visible):self.calls.append((hwnd,visible));self.visible=visible


class VisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=guard.ROOT);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.patch1=patch.object(guard,'ROOT',self.root);self.patch1.start();self.addCleanup(self.patch1.stop)
        self.patch2=patch.object(guard,'CONFIG',self.root/'config.json');self.patch2.start();self.addCleanup(self.patch2.stop)
        self.patch3=patch('app.local_runtime.enabled',return_value=False);self.patch3.start();self.addCleanup(self.patch3.stop)
        self.native=FakeNative();guard.bind(self.native,42)
        for name in ('live-service-status.json','listener-status.json'):
            guard.write(self.root/name,{'state':'ready','updated_at':time.time()})

    def test_fault_restores_and_latches_visible_after_recovery(self):
        guard.command('hide',self.native);self.assertFalse(self.native.visible)
        guard.write(self.root/'listener-status.json',{'state':'waiting_manual_login','updated_at':time.time()})
        self.assertTrue(guard.guard_step(self.native)['attention_latched'])
        self.assertTrue(self.native.visible)
        guard.write(self.root/'listener-status.json',{'state':'listening','updated_at':time.time()})
        guard.guard_step(self.native);self.assertTrue(self.native.visible)

    def test_pid_reuse_never_hides_unrelated_window(self):
        self.native.binding=dict(self.native.binding,created_filetime=999)
        with self.assertRaises(RuntimeError):guard.command('hide',self.native)
        self.assertEqual(self.native.calls,[])

    def test_stale_heartbeat_refuses_hiding(self):
        guard.write(self.root/'live-service-status.json',{'updated_at':time.time()-200,'state':'ready'})
        with self.assertRaisesRegex(RuntimeError,'heartbeat_stale'):guard.command('hide',self.native)
        self.assertEqual(self.native.calls,[])

    def test_manual_show_is_not_rehidden(self):
        guard.command('hide',self.native);guard.command('show',self.native)
        guard.guard_step(self.native);self.assertTrue(self.native.visible)


if __name__=='__main__':unittest.main()
