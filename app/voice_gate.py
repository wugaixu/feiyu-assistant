"""Whale-only speech gate.

The local GPT-SoVITS plugin registers a plain LLM tool, so any channel could
call it. Speech is only allowed in a Whale owner turn that carries the one-use
permit inserted by the worker; the plugin consults this module before hiding
or running the tool.
"""
import json
import re
import sqlite3
import time
from pathlib import Path

ROOT = Path(r'__RUNTIME_ROOT__')
TOKEN = re.compile(r'\[鲸聊语音许可:([0-9a-f]{48})\]')


def token_of(text):
    match = TOKEN.search(str(text or ''))
    return match.group(1) if match else ''


def permit_valid(token, now=None):
    """Read-only check: unexpired permit for a processing Whale task."""
    if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{48}', token):
        return False
    stamp = time.time() if now is None else now
    try:
        db = sqlite3.connect('file:' + (ROOT / 'assistant.sqlite3').as_posix() + '?mode=ro', uri=True, timeout=1)
        try:
            row = db.execute(
                '''SELECT 1 FROM whale_voice_permits p JOIN live_tasks t ON t.id=p.task_id
                   WHERE p.token=? AND p.expires>? AND t.status='processing'
                   AND json_extract(t.raw_json,'$.transport') IN ('whale','qq') ''',
                (token, stamp)).fetchone()
            return bool(row)
        finally:
            db.close()
    except (sqlite3.Error, OSError):
        return False


def allowed(text, now=None):
    """True only for a Whale owner turn carrying a valid permit."""
    token = token_of(text)
    return bool(token) and permit_valid(token, now=now)


def status_text(text, now=None):
    """Short note for the model when speech is not permitted."""
    if not token_of(text):
        return '这一轮没有鲸聊语音许可，用文字回复即可，不要声称已经发语音。'
    if not permit_valid(token_of(text), now=now):
        return '鲸聊语音许可已过期或无效，这一轮用文字回复。'
    return ''
