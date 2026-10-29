"""One-use synthesis permission for an actually claimed Whale owner turn."""
import base64
import json
import re
import secrets
import subprocess
import time
import uuid
from app.audio_sender import ROOT,audio_dir,store_pcm


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS whale_voice_permits(token TEXT PRIMARY KEY,task_id TEXT UNIQUE NOT NULL,session_id TEXT NOT NULL,expires REAL NOT NULL,used INTEGER NOT NULL DEFAULT 0)')
    columns={r[1] for r in db.execute('PRAGMA table_info(whale_voice_permits)')}
    for column in ('text','path'):
        if column not in columns:db.execute('ALTER TABLE whale_voice_permits ADD COLUMN '+column+' TEXT')
    db.execute('''CREATE TABLE IF NOT EXISTS whale_voice_captions(
        task_id TEXT NOT NULL,sha256 TEXT NOT NULL,text TEXT NOT NULL,
        PRIMARY KEY(task_id,sha256))''')
    db.commit()


def permit(db,task_id,session_id):
    from app.sdk_receiver import OWNER
    row=db.execute('SELECT owner,status,raw_json FROM live_tasks WHERE id=?',(task_id,)).fetchone()
    if not row or row[0]!=OWNER or row[1]!='processing':raise ValueError('Claimed owner turn required')
    raw=json.loads(row[2])
    if raw.get('transport') not in ('whale','qq'):return ''
    if raw.get('direction')!='incoming':
        from app.diary_proactive import target
        if raw.get('origin')!='diary_proactive' or not db.execute("SELECT 1 FROM sqlite_master WHERE name='diary_proactive_jobs'").fetchone():return ''
        job=db.execute("SELECT destination,expires,status FROM diary_proactive_jobs WHERE id=?",(task_id,)).fetchone()
        if not job or job[2]!='generating' or job[1]<=time.time() or json.loads(job[0])!=target() or json.loads(job[0]).get('session_id')!=session_id:return ''
    schema(db)
    with db:db.execute('INSERT OR IGNORE INTO whale_voice_permits(token,task_id,session_id,expires) VALUES(?,?,?,?)',(secrets.token_hex(24),task_id,session_id,time.time()+600))
    return db.execute('SELECT token FROM whale_voice_permits WHERE task_id=?',(task_id,)).fetchone()[0]


def extract_token(text):
    match=re.search(r'\[鲸聊语音许可:([0-9a-f]{48})\]',str(text))
    return match.group(1) if match else None


def synthesize(db,token,text):
    if not isinstance(text,str) or not 1<=len(text.strip())<=400:raise ValueError('语音文字需1–400字')
    from app.sdk_receiver import OWNER
    schema(db)
    with db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('''SELECT p.task_id FROM whale_voice_permits p JOIN live_tasks t ON t.id=p.task_id
            JOIN chat_dispatch d ON d.task_id=t.id WHERE p.token=? AND p.used=0 AND p.expires>?
            AND t.owner=? AND t.status='processing' AND json_extract(t.raw_json,'$.transport') IN ('whale','qq')
            AND d.status IN ('attempted','queued') AND json_extract(d.detail,'$.provider')='astrbot'
            AND json_extract(d.detail,'$.session_id')=p.session_id''',(token,time.time(),OWNER)).fetchone()
        if not row:raise ValueError('本轮语音许可无效、已使用或过期')
        db.execute('UPDATE whale_voice_permits SET used=1 WHERE token=?',(token,))
    path=speak(text.strip(),row[0])
    with db:db.execute('UPDATE whale_voice_permits SET text=?,path=? WHERE token=?',(text.strip(),path,token))
    return path


def known_text(db,task_id,path):
    # The audio hash is for the same canonical PCM the outgoing downloader
    # produces, so compressed plugin audio cannot attach text to another clip.
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='whale_voice_captions'").fetchone():
        from app.audio_sender import validate_audio
        _,digest,_=validate_audio(path)
        row=db.execute('SELECT text FROM whale_voice_captions WHERE task_id=? AND sha256=?',(task_id,digest)).fetchone()
        if row:return row[0]
    row=db.execute('SELECT text FROM whale_voice_permits WHERE task_id=? AND path=? AND used=1',(task_id,str(path))).fetchone()
    return row[0] if row else ''


def speak(text,task_id):
    """Offline Windows voice. Text travels only through JSON stdin, never shell code."""
    directory=audio_dir();directory.mkdir(parents=True,exist_ok=True)
    path=directory/(uuid.uuid4().hex+'.tts.wav')
    script='''$ErrorActionPreference='Stop'
[Console]::InputEncoding=New-Object System.Text.UTF8Encoding($false)
$v=[Console]::In.ReadToEnd() | ConvertFrom-Json
Add-Type -AssemblyName System.Speech
$s=New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
$s.SelectVoice('Microsoft Huihui Desktop')
$format=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile($v.path,$format);$s.Speak([string]$v.text)
} finally {$s.Dispose()}
'''
    encoded=base64.b64encode(script.encode('utf-16le')).decode('ascii')
    try:
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',encoded],
            input=json.dumps({'text':text,'path':str(path)},ensure_ascii=False).encode('utf-8'),
            capture_output=True,timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode or not path.exists():raise ValueError('本机语音合成失败')
        return store_pcm(path.read_bytes(),task_id)
    finally:path.unlink(missing_ok=True)
