"""Bounded, decoded audio assets for Whale only; never a Douyin upload contract."""
import hashlib
import io
import re
import subprocess
import uuid
import wave
from pathlib import Path

ROOT=Path(r'__RUNTIME_ROOT__')
MAX_BYTES=32*1024*1024
MAX_SECONDS=600


def audio_dir():return ROOT/'outgoing-audio'


def ffmpeg_exe():
    # Same binary in the worker and AstrBot's separate environment. The latter
    # need not install imageio_ffmpeg merely to retain TTS captions.
    bundled=ROOT/'tools/gpt-sovits/ffmpeg-shared/ffmpeg.exe'
    if bundled.is_file():return str(bundled)
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def inspect_pcm(raw):
    try:
        with wave.open(io.BytesIO(raw),'rb') as stream:
            if stream.getnchannels()!=1 or stream.getsampwidth()!=2 or stream.getframerate()!=16000:raise ValueError('Canonical PCM required')
            frames=stream.getnframes();duration=frames/16000
            if not 0<duration<=MAX_SECONDS or len(stream.readframes(frames))!=frames*2:raise ValueError('Invalid audio length')
    except (wave.Error,EOFError) as exc:raise ValueError('Invalid audio') from exc
    return duration


def store_pcm(raw,task_id):
    if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_BYTES or not re.fullmatch(r'[0-9a-f]{32}',task_id):raise ValueError('Invalid PCM asset')
    inspect_pcm(raw)
    directory=audio_dir();directory.mkdir(parents=True,exist_ok=True)
    target=directory/('astrbot-'+task_id+'-'+hashlib.sha256(raw).hexdigest()[:16]+'.wav')
    if target.exists():
        if target.read_bytes()!=raw:raise ValueError('Audio filename collision')
        validate_audio(target);return str(target)
    created=False
    try:
        with target.open('xb') as stream:stream.write(raw)
        created=True
        validate_audio(target)
    except Exception:
        if created:target.unlink(missing_ok=True)
        raise
    return str(target)


def validate_audio(path):
    path=Path(path).resolve(strict=True)
    if path.parent!=audio_dir().resolve() or not re.fullmatch(r'astrbot-[0-9a-f]{32}-[0-9a-f]{16}\.wav',path.name):
        raise ValueError('Managed outgoing audio required')
    raw=path.read_bytes() if path.is_file() and 0<path.stat().st_size<=MAX_BYTES else b''
    duration=inspect_pcm(raw)
    return path,hashlib.sha256(raw).hexdigest(),duration


def prepare(raw,task_id):
    if not re.fullmatch(r'[0-9a-f]{32}',task_id) or not isinstance(raw,bytes) or not 0<len(raw)<=MAX_BYTES:
        raise ValueError('Invalid audio attachment')
    try:inspect_pcm(raw)
    except ValueError:pass
    else:return store_pcm(raw,task_id)
    from app.resource_storage import LOCK
    # Prevent cleanup seeing an unqueued file during the short decode step.
    with LOCK:
        directory=audio_dir();directory.mkdir(parents=True,exist_ok=True)
        key=uuid.uuid4().hex;source=directory/(key+'.source');dest=directory/(key+'.wav')
        try:
            source.write_bytes(raw)
            result=subprocess.run([ffmpeg_exe(),'-nostdin','-v','error','-n',
                '-protocol_whitelist','file,pipe','-format_whitelist','wav,mp3,aac,mov,ogg,flac,matroska,webm','-i',str(source),'-map','0:a:0','-vn','-ac','1','-ar','16000',
                '-t',str(MAX_SECONDS+1),'-c:a','pcm_s16le',str(dest)],capture_output=True,timeout=40,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode:raise ValueError('Audio decode failed')
            return store_pcm(dest.read_bytes(),task_id)
        finally:
            source.unlink(missing_ok=True);dest.unlink(missing_ok=True)
