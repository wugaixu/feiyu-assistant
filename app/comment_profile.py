"""Cross-process exclusive access to the dedicated comment browser profile."""
from app.live_service import ROOT


class ProfileBusy(RuntimeError):pass


class ProfileLease:
    def __enter__(self):
        import msvcrt
        self.handle=(ROOT/'comment-profile.lock').open('a+b')
        if self.handle.tell()==0:self.handle.write(b'0');self.handle.flush()
        self.handle.seek(0)
        try:msvcrt.locking(self.handle.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            self.handle.close();raise ProfileBusy('评论浏览器正在使用中，请稍后检查') from None
        return self

    def __exit__(self,*args):
        import msvcrt
        self.handle.seek(0);msvcrt.locking(self.handle.fileno(),msvcrt.LK_UNLCK,1);self.handle.close()
