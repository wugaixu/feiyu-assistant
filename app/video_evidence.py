"""Whole-timeline reference frames and subtitle strips; no model calls."""
import hashlib
import json
import re
import subprocess
from pathlib import Path
import imageio_ffmpeg
from PIL import Image, ImageOps, ImageDraw


def select_times(duration, count=9, scenes=()):
    if duration<=0 or not 1<=count<=12: raise ValueError('Invalid sampling budget')
    width=duration/count
    selected=[]
    for i in range(count):
        center=(i+0.5)*width
        near=[t for t in scenes if 0<=t<duration and abs(t-center)<width/2]
        chosen=min(near,key=lambda t:abs(t-center)) if near else center
        if any(abs(t-chosen)<0.1 for t in selected): chosen=center
        selected.append(round(chosen,3))
    return selected


def extract(source,root,duration_hint=None,count=9):
    if not 1<=count<=12:raise ValueError('Invalid sampling count')
    source=Path(source).resolve(strict=True)
    with source.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
    dest=Path(root)/digest[:16]/('timeline-v2' if count==9 else f'timeline-v2-samples-{count}');dest.mkdir(parents=True,exist_ok=True)
    manifest=dest/'evidence.json'
    if manifest.exists(): return json.loads(manifest.read_text(encoding='utf-8'))
    ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    probe=subprocess.run([ffmpeg,'-nostdin','-hide_banner','-i',str(source)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=10,creationflags=flags)
    match=re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)',probe.stderr)
    if match:duration=int(match[1])*3600+int(match[2])*60+float(match[3])
    elif isinstance(duration_hint,(int,float)) and 0<duration_hint<=600:duration=float(duration_hint)
    else:raise ValueError('Could not determine video duration')
    scenes=[]
    try:
        scan=subprocess.run([ffmpeg,'-nostdin','-hide_banner','-i',str(source),'-vf',"scale=160:-2,select='gt(scene,0.15)',showinfo",'-an','-f','null','-'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20,creationflags=flags)
        if scan.returncode==0: scenes=[float(t) for t in re.findall(r'pts_time:([\d.]+)',scan.stderr)]
    except subprocess.TimeoutExpired: pass
    times=select_times(duration,count,scenes)
    frames=[]
    for i,t in enumerate(times):
        path=dest/f'frame_{i+1:02}.jpg'
        if not path.exists():
            result=subprocess.run([ffmpeg,'-nostdin','-v','error','-n','-ss',str(t),'-i',str(source),'-frames:v','1','-vf','scale=960:-2',str(path)],capture_output=True,timeout=10,creationflags=flags)
            if result.returncode or not path.is_file() or path.stat().st_size==0:
                # A short/low-FPS live clip can end before its declared duration.
                # Retry only local decoding, recording the actual earlier time.
                t=round(max(0,t-.25),3);times[i]=t
                subprocess.run([ffmpeg,'-nostdin','-v','error','-y','-ss',str(t),'-i',str(source),'-frames:v','1','-vf','scale=960:-2',str(path)],capture_output=True,timeout=10,check=True,creationflags=flags)
        frames.append(str(path))
    cells=[];strips=[]
    for i,name in enumerate(frames):
        with Image.open(name) as im:
            image=im.convert('RGB')
            tile=ImageOps.contain(image,(480,300))
            cell=Image.new('RGB',(480,326),'white');cell.paste(tile,((480-tile.width)//2,0));ImageDraw.Draw(cell).text((8,307),f'{i+1}: {times[i]:.1f}s',fill='black');cells.append(cell)
            # Keep the bottom 43% so subtitles above platform overlays are included.
            crop=image.crop((0,int(image.height*.55),image.width,int(image.height*.98)))
            crop=ImageOps.contain(crop,(960,320))
            strip=Image.new('RGB',(960,crop.height+24),'white');strip.paste(crop,(0,24));ImageDraw.Draw(strip).text((8,4),f'{times[i]:.1f}s',fill='black');strips.append(strip)
    sheet=Image.new('RGB',(1440,326*((count+2)//3)),'white')
    for i,cell in enumerate(cells): sheet.paste(cell,((i%3)*480,(i//3)*326))
    sheet_path=dest/'contact-sheet.jpg';sheet.save(sheet_path,quality=88)
    strip_sheet=Image.new('RGB',(960,sum(s.height for s in strips)),'white')
    y=0
    for strip in strips: strip_sheet.paste(strip,(0,y));y+=strip.height
    strip_path=dest/'subtitle-strip.jpg';strip_sheet.save(strip_path,quality=92)
    record={'status':'frames_available','source_file':str(source),'sha256':digest,'frames':frames,
            'duration_seconds':duration,'sample_times_seconds':times,'contact_sheet':str(sheet_path),
            'subtitle_strip':str(strip_path),'scene_candidates':len(scenes),
            'evidence_limit':f'{count} samples across the whole timeline, snapped to nearby scene changes; not complete subtitles or audio transcription',
            'transcript_available':False,'sampling_version':2}
    manifest.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return record
