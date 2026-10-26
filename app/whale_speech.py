"""Bounded WAV-to-text worker using the project's independent SenseVoice assets."""
import base64
import io
import json
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from app.live_service import ROOT


def transcribe(audio):
    if not isinstance(audio,bytes) or not 44<=len(audio)<=2100000:raise ValueError('Invalid WAV size')
    with wave.open(io.BytesIO(audio),'rb') as stream:
        frames=stream.getnframes()
        if stream.getnchannels()!=1 or stream.getframerate()!=16000 or stream.getsampwidth()!=2 or frames>16000*61:raise ValueError('Expected bounded 16 kHz mono PCM16')
        samples=stream.readframes(frames)
        if len(samples)!=frames*2:raise ValueError('Truncated WAV')
    directory=ROOT/'whale-data/stt-work';directory.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory,suffix='.wav',delete=False) as f:f.write(audio);path=Path(f.name)
    try:
        node=shutil.which('node')
        if not node:raise ValueError('Node.js unavailable')
        run=subprocess.run([node,str(Path(__file__).with_name('sensevoice_worker.cjs'))],
            input=json.dumps({'runtime':str(ROOT/'tools/sensevoice'),'files':[str(path)]}),capture_output=True,
            text=True,encoding='utf-8',errors='replace',timeout=135,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if run.returncode:raise ValueError('Local SenseVoice inference failed')
        result=json.loads(run.stdout)
        if result.get('ok') is not True or len(result.get('clips',[]))!=1:raise ValueError('Invalid recognition result')
        text=' '.join(s.get('text','').strip() for s in result['clips'][0]['segments'] if s.get('text','').strip())
        return {'text':text,'durationMs':round(frames/16),'engine':'project_local_sensevoice'}
    finally:path.unlink(missing_ok=True)


def main():
    data=sys.stdin.buffer.read(3000001)
    if len(data)>3000000:raise ValueError('Audio payload too large')
    request=json.loads(data)
    audio=base64.b64decode(request['audio'],validate=True)
    print(json.dumps(transcribe(audio),ensure_ascii=False))


if __name__=='__main__':main()
