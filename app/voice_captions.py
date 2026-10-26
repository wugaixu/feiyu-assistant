"""Actual TTS text bound to a permitted task and canonical audio; no synthesis."""


def schema(db):
    db.execute("CREATE TABLE IF NOT EXISTS whale_voice_captions(task_id TEXT NOT NULL,sha256 TEXT NOT NULL,text TEXT NOT NULL,PRIMARY KEY(task_id,sha256))")
    db.commit()


def remember(db,token,raw,text):
    """Bind actual synthesized text to one permitted turn and decoded audio."""
    if not isinstance(text,str) or not 1<=len(text.strip())<=1500:raise ValueError('Invalid synthesized text')
    from app.plugin_audio import authorized
    from app import audio_sender
    from app.resource_storage import LOCK
    task,late=authorized(db,token)
    if late:raise ValueError('Speech turn already finished')
    schema(db)
    with LOCK:
        path=audio_sender.prepare(raw,task)
        _,digest,_=audio_sender.validate_audio(path)
        with db:
            db.execute('BEGIN IMMEDIATE')
            if authorized(db,token)!=(task,False):raise ValueError('Speech turn changed')
            old=db.execute('SELECT text FROM whale_voice_captions WHERE task_id=? AND sha256=?',(task,digest)).fetchone()
            if old and old[0]!=text.strip():raise ValueError('Audio caption conflict')
            db.execute('INSERT OR IGNORE INTO whale_voice_captions VALUES(?,?,?)',(task,digest,text.strip()))
    return path


def remember_plugin(event,result):
    """Installed GPT-SoVITS adapter: no text from neighboring chat messages."""
    from app.voice_gate import token_of
    token=token_of(event.get_message_str())
    if event.get_platform_name()!='webchat' or not token:return
    if not result.ok or not result.data or not result.text:return
    import sqlite3
    from app.audio_sender import ROOT
    db=sqlite3.connect(ROOT/'assistant.sqlite3',timeout=10)
    try:remember(db,token,result.data,result.text)
    finally:db.close()

