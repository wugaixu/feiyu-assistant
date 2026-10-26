"""Keep the DSH session-bridge registration across DSH updates.

A DSH update resets ``cordis.patch.yml``; without the insert block the local
connector on 127.0.0.1:8794 disappears and the management console can no longer
see DSH sessions. This module detects the loss, backs up the current file and
re-adds the block; it never edits unrelated entries.
"""
import json
import re
import shutil
import time
from pathlib import Path

PATCH = Path(r'__DSH_HOME__\profiles\desktop\cordis.patch.yml')
LOG = Path(r'__RUNTIME_ROOT__\dsh-registry.log')
PLUGIN_ID = 'douyin-local-session-bridge'
BRIDGE = '__PROJECT_URI__/dsh_bridge/index.mjs'
BLOCK = ('- insert:\n'
         '    - id: ' + PLUGIN_ID + '\n'
         '      name: "' + BRIDGE + '"\n')
EMPTY = re.compile(r'(?m)^[ \t]*\[\][ \t]*$')
_checked = 0.0


def inspect():
    """Read-only state without writing anything."""
    if not PATCH.parent.is_dir():
        return {'state': 'unavailable', 'path': str(PATCH)}
    try:
        text = PATCH.read_text(encoding='utf-8') if PATCH.is_file() else ''
    except OSError:
        return {'state': 'unreadable', 'path': str(PATCH)}
    return {'state': 'present' if PLUGIN_ID in text else 'missing', 'path': str(PATCH), 'text': text}


def build(text):
    """Return the file body with the insert block restored."""
    if not text.strip():
        return BLOCK
    if EMPTY.search(text):
        return EMPTY.sub(BLOCK.rstrip('\n'), text, count=1)
    return text.rstrip('\n') + '\n' + BLOCK


def _valid(text):
    try:
        import yaml
    except Exception:
        return PLUGIN_ID in text
    try:
        value = yaml.safe_load(text)
    except Exception:
        return False
    return (isinstance(value, list)
            and any(isinstance(item, dict) and 'insert' in item for item in value)
            and PLUGIN_ID in text)


def ensure(recheck_seconds=300):
    """Restore a lost registration at most once per interval; always safe."""
    global _checked
    now = time.monotonic()
    if _checked and now - _checked < recheck_seconds:
        return {'state': 'skipped', 'changed': False}
    report = inspect()
    if report['state'] != 'missing':
        _checked = now
        return {'state': report['state'], 'changed': False, 'path': report['path']}
    text = report.get('text', '')
    body = build(text)
    if not _valid(body):
        _checked = now
        result = {'state': 'invalid', 'changed': False, 'path': str(PATCH)}
        _write_log(result)
        return result
    backup = None
    try:
        if PATCH.is_file():
            backup = PATCH.with_name(PATCH.name + '.backup-' + time.strftime('%Y%m%d-%H%M%S'))
            shutil.copy2(PATCH, backup)
        temp = PATCH.with_name(PATCH.name + '.tmp')
        temp.write_text(body, encoding='utf-8')
        temp.replace(PATCH)
        confirmed = PATCH.read_text(encoding='utf-8')
    except OSError:
        _checked = now
        return {'state': 'write_failed', 'changed': False, 'path': str(PATCH)}
    restored = PLUGIN_ID in confirmed
    _checked = now
    result = {'state': 'restored' if restored else 'unconfirmed', 'changed': restored,
              'path': str(PATCH), 'backup': str(backup) if backup else None}
    _write_log(result)
    return result


def _write_log(value):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({**value, 'time': time.time()}, ensure_ascii=False) + '\n')
    except OSError:
        pass
