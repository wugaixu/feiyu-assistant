"""Open the local voice preview page; starts the on-demand service first.

Local-only: writes the demand signal the manager polls, waits for the loopback
port, then opens the preview page. Never sends a chat message.
"""
import socket
import sys
import time
import webbrowser

from app import gpt_sovits


def wait_ready(seconds=90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', 9880), timeout=.5):
                return True
        except OSError:
            time.sleep(1)
    return False


def main():
    if not gpt_sovits.settings().get('enabled'):
        return 2
    if not wait_ready(0.1):
        gpt_sovits.wake('preview')
        if not wait_ready(90):
            return 1
    webbrowser.open('http://127.0.0.1:9880/voice/')
    return 0


if __name__ == '__main__':
    sys.exit(main())
