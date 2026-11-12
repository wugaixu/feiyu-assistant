"""ComfyUI with an idle watchdog independent of its launching UI.

Only this interpreter exits. External backends are never adopted or killed.
"""
import os
import runpy
import sys
import threading
import time

from app import local_images


def idle_check(started, last_busy, now):
    """An unknown queue prevents exit; busy work renews the idle window."""
    queue = local_images.api('/queue', timeout=3)
    if not isinstance(queue, dict) or any(
        not isinstance(queue.get(key), list)
        for key in ('queue_running', 'queue_pending')
    ):
        raise ValueError('ComfyUI queue not confirmed')
    if queue['queue_running'] or queue['queue_pending']:
        return False, now
    latest = max(started, last_busy, local_images.last_activity() or started)
    return now - latest >= local_images.idle_minutes() * 60, last_busy


def watch_idle(stop, interval=10):
    started = last_busy = time.time()
    while not stop.wait(interval):
        try:
            expired, last_busy = idle_check(started, last_busy, time.time())
        except Exception:
            last_busy = time.time()
            continue
        if not expired:
            continue
        try:
            (local_images.BASE / 'backend-idle-exit.log').write_text(
                time.strftime('%Y-%m-%d %H:%M:%S') + ' backend-self-idle\n',
                encoding='utf-8',
            )
        except OSError:
            pass
        # Torch may retain threads after HTTP shutdown. This exits only our own
        # interpreter, after its real generation queue has been confirmed empty.
        os._exit(0)


def main():
    # Lifetime lock prevents competing launchers racing during slow startup.
    lock = (local_images.BASE / 'backend-instance.lock').open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b'0')
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return
        else:
            import fcntl
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return
        try:
            local_images.api('/system_stats', timeout=1)
        except Exception:
            pass
        else:
            return
        main_file = local_images.BASE / 'ComfyUI/main.py'
        local_images.touch('backend-start')
        stop = threading.Event()
        threading.Thread(target=watch_idle, args=(stop,), daemon=True,
                         name='image-backend-idle').start()
        os.chdir(main_file.parent)
        sys.path.insert(0, str(main_file.parent))
        # ComfyUI has its own top-level "app" package. The watchdog already
        # retains its local_images module; release our package names so the
        # vendor entry point imports its own app.governance/app.frontend.
        for name in list(sys.modules):
            if name == 'app' or name.startswith('app.'):
                del sys.modules[name]
        sys.argv = [str(main_file), '--listen', '127.0.0.1', '--port', '8188',
                    '--lowvram', '--preview-method', 'none', '--disable-auto-launch']
        try:
            runpy.run_path(str(main_file), run_name='__main__')
        finally:
            stop.set()
    finally:
        lock.close()


if __name__ == '__main__':
    main()
