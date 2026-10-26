"""Offline voice asset preparation. No login, upload, message body or sending.

AAC/M4A matches observed incoming samples, not a verified upload contract.
File hashes and waveform measurements are research data, not platform fields.
"""
import hashlib
import json
import subprocess
import uuid
import wave
from pathlib import Path

import imageio_ffmpeg

ROOT = Path(r'__RUNTIME_ROOT__')
FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def prepare(source, *, max_seconds=60):
    source = Path(source).resolve(strict=True)
    if not source.is_file() or not 0 < source.stat().st_size <= 64 * 1024 * 1024:
        raise ValueError('Audio source must be a file of 1 byte to 64 MiB')
    if not isinstance(max_seconds, int) or isinstance(max_seconds, bool) or not 1 <= max_seconds <= 600:
        raise ValueError('Research decode limit must be 1 to 600 seconds')
    directory = ROOT / 'outgoing-audio' / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    pcm = directory / 'decoded.wav'
    audio = directory / 'voice.m4a'
    report = {'schema_version': 1, 'status': 'failed', 'upload_verified': False,
              'sent': False, 'platform_resource_uri': None}
    def run(args):
        result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error',
                                 '-n', *args], capture_output=True, timeout=60, creationflags=FLAGS)
        if result.returncode:
            raise ValueError('Voice audio conversion failed')
    try:
        # Decode slightly beyond the budget so long clips are rejected, not
        # silently shortened into a different utterance.
        run(['-i', str(source), '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '44100',
             '-t', str(max_seconds + 1), '-c:a', 'pcm_s16le', str(pcm)])
        with wave.open(str(pcm), 'rb') as stream:
            duration = stream.getnframes() / stream.getframerate()
        if not 0 < duration <= max_seconds:
            raise ValueError('Voice audio exceeds research duration budget or is empty')
        run(['-i', str(pcm), '-c:a', 'aac', '-b:a', '96k', '-movflags', '+faststart', str(audio)])
        # Actually decode the encoded output, rather than trust its extension.
        run(['-i', str(audio), '-map', '0:a:0', '-f', 'null', '-'])
        with audio.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        with audio.open('rb') as stream:
            md5 = hashlib.file_digest(stream, 'md5').hexdigest()
        report.update(status='prepared_offline', path=str(audio), bytes=audio.stat().st_size,
                      codec='aac', container='m4a', sample_rate=44100, channels=1,
                      decoded_duration_seconds=duration, sha256=sha, local_file_md5=md5,
                      evidence_limit='Local MD5/duration are not verified platform payload fields. '
                                     'AAC/M4A encoding alone does not prove server compatibility.')
        return {**report, 'manifest_path': str(directory / 'manifest.json')}
    except Exception as exc:
        report['reason'] = str(exc)
        raise
    finally:
        (directory / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
