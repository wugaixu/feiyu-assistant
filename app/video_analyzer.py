"""Decode explicitly supplied local media. Never resolves or downloads share URLs."""
import hashlib
import json
import subprocess
from pathlib import Path
import imageio_ffmpeg


def analyze_local_video(source, reference_root):
    source = Path(source).resolve(strict=True)
    if not source.is_file():
        raise ValueError('A regular media file is required')
    with source.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    dest = Path(reference_root) / digest[:16]
    dest.mkdir(parents=True, exist_ok=True)
    manifest_path = dest / 'reference.json'
    if manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding='utf-8'))
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    try:
        result = subprocess.run([
            ffmpeg, '-nostdin', '-hide_banner', '-n', '-i', str(source),
            '-vf', 'fps=1/5,scale=640:-2', '-frames:v', '12',
            str(dest / 'frame_%02d.jpg')
        ], capture_output=True, encoding='utf-8', errors='replace', timeout=120)
        log, success = result.stderr, result.returncode == 0
    except subprocess.TimeoutExpired:
        log, success = 'FFmpeg decoding exceeded the 120-second timeout', False
    (dest / 'decode.log').write_text(log, encoding='utf-8')
    frames = [str(p) for p in sorted(dest.glob('frame_*.jpg'))]
    record = {
        'sha256': digest, 'source_file': str(source),
        'original_share_url': None, 'task_id': None,
        'status': 'frames_available' if success and frames else 'decode_failed',
        'frames': frames, 'frame_selection': 'one sample per 5 seconds; at most 12',
        'title': None, 'author': None, 'user_instruction': None,
        'transcript': None, 'subtitles': None, 'summary': None,
        'uncertainties': ['Speech recognition and subtitle extraction have not been performed',
                          'Sampled frames do not describe every moment of the video'],
        'failure_reason': None if success and frames else log[-2000:]
    }
    manifest_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return record
