"""Installed Windows OCR: sampled screen text, never claimed as full speech."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from app.douyin_content import ROOT, transcript_path


def extract(video_id):
    dest=transcript_path(video_id).parent
    manifest=dest/'frames-v2.json' if (dest/'frames-v2.json').exists() else dest/'frames.json'
    if not manifest.exists(): return {'status':'frames_required','video_id':video_id}
    target=dest/('frame-ocr-v2.json' if manifest.name=='frames-v2.json' else 'frame-ocr.json')
    if target.exists(): return {'status':'sampled_text_available','video_id':video_id,'cached':True,'ocr_file':str(target)}
    record=json.loads(manifest.read_text(encoding='utf-8'))
    paths=record['frames'][:12]
    from app.resource_storage import resource_dirs
    allowed=[(root/'references').resolve() for root in resource_dirs('content',ROOT)]+[root.resolve() for root in resource_dirs('analysis',ROOT)]
    for name in paths:
        path=Path(name).resolve(strict=True)
        if not any(path.is_relative_to(root) for root in allowed) or path.suffix.lower() not in ('.jpg','.png'): raise ValueError('Unapproved frame path')
    powershell=Path(os.environ['WINDIR'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    script=(Path(__file__).parent/'ocr_frames_windows.ps1').read_text(encoding='utf-8-sig')
    env=dict(os.environ,DOUYIN_OCR_FRAMES=json.dumps(paths))
    try:
        proc=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-Command',script],
            env=env,capture_output=True,text=True,encoding='utf-8-sig',timeout=40,check=True)
        rows=json.loads(proc.stdout)
        if isinstance(rows,dict): rows=[rows]
        segments=[]
        for index,row in enumerate(rows):
            lines=[re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])','',s) for s in row.get('lines',[])]
            if lines: segments.append({'sample_index':index,'text':'\n'.join(lines),'frame':row['path']})
        if not segments: return {'status':'no_text_recognized','video_id':video_id}
        value={'video_id':video_id,'source':'sampled_frame_ocr','segments':segments,
            'text':'\n\n'.join(s['text'] for s in segments),'sha256':hashlib.sha256(proc.stdout.encode('utf-8')).hexdigest(),
            'uncertainty':'OCR of at most twelve sampled frames only, including slide text; not full subtitles, audio transcription or full-video coverage.'}
        target.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        return {'status':'sampled_text_available','video_id':video_id,'ocr_file':str(target),'characters':len(value['text']),'frame_count':len(segments),'source':'sampled_frame_ocr'}
    except Exception as exc:
        return {'status':'unavailable','video_id':video_id,'reason':'Installed Windows OCR failed ('+type(exc).__name__+')','automatic_retry':False}
