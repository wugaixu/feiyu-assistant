"""Run the verified DouK 5.8 binary in a private, bounded work directory."""
import hashlib,json,re,shutil,sqlite3,subprocess,sys
from pathlib import Path

ROOT=Path(r'__RUNTIME_ROOT__')
# The binary occasionally needs longer than a minute for multi-image posts;
# the outer adapter allows a little more than this worker's own budget.
DOUK_TIMEOUT_SECONDS=180
DOUK_HTTP_TIMEOUT_SECONDS=20


def expected_count(value):
    if isinstance(value,int) and not isinstance(value,bool) and 1<=value<=20:return value
    return None


def collect_output(directory,before,video_id,expected_images=None):
    """Classify actual new files; video covers are never accepted as a gallery."""
    candidates=[p for p in directory.rglob('*') if p.is_file() and p.resolve() not in before]
    expected=expected_count(expected_images)
    videos=[p for p in candidates if p.suffix.lower()=='.mp4']
    if any(not 0<p.stat().st_size<=256*1024*1024 for p in videos):raise ValueError('Video output exceeds byte limit or is empty')
    def info(p):
        return {'status':'saved','path':str(p),'bytes':p.stat().st_size,
            'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'source':'douk_5.8'}
    if videos and any('-实况-' in p.name for p in videos):
        # Only the numbered clips of one explicitly labelled live-photo work
        # form a collection. Unrelated/mixed MP4 outputs remain ambiguous.
        numbered=[(re.fullmatch(r'(.+-实况-.+)_(\d+)',p.stem),p) for p in videos]
        if len(videos)>20 or any(not m for m,p in numbered):raise ValueError('Invalid live-photo outputs')
        if len({(str(p.parent.resolve()),m[1]) for m,p in numbered})!=1:raise ValueError('Mixed live-photo works')
        numbered.sort(key=lambda item:int(item[0][2]))
        if [int(m[2]) for m,p in numbered]!=list(range(1,len(videos)+1)):raise ValueError('Incomplete live-photo sequence')
        if sum(p.stat().st_size for p in videos)>256*1024*1024:raise ValueError('Live-photo collection exceeds byte limit')
        return {'status':'saved','media_type':'live_photo_collection','files':[info(p) for m,p in numbered],
            'clip_count':len(videos),'video_id':video_id,'automatic_retry':False,'source':'douk_5.8',
            'completeness':'Locally downloaded numbered clips; original platform count not independently verified'}
    if len(videos)==1:
        return {**info(videos[0]),'media_type':'video','video_id':video_id,'automatic_retry':False}
    if videos:raise ValueError('Ambiguous video outputs')
    images=[p for p in candidates if '-图集-' in p.name and p.suffix.lower() in ('.jpg','.jpeg','.png','.webp','.gif','.bmp','.tiff')]
    if not images:raise ValueError('No video or gallery output')
    images.sort(key=lambda p:[int(x) if x.isdigit() else x for x in re.split(r'(\d+)',p.name)])
    if sum(p.stat().st_size for p in images)>64*1024*1024:raise ValueError('Gallery exceeds byte limit')
    from PIL import Image
    files=[]
    for p in images:
        with Image.open(p) as image:
            if image.width*image.height>50_000_000:raise ValueError('Image exceeds pixel limit')
            fmt,size=image.format,list(image.size);image.verify()
        files.append({**info(p),'format':fmt,'size':size})
    partial=bool(expected and len(files)<expected)
    completeness=(f'Downloaded {len(files)} of {expected} images; the rest did not arrive'
                  if partial else 'Downloaded image files; not a guarantee of original gallery count')
    return {'status':'saved','media_type':'image_gallery','files':files,'image_count':len(files),
        'expected_images':expected,'partial':partial,
        'video_id':video_id,'automatic_retry':False,'source':'douk_5.8','completeness':completeness}


def salvage_timeout(directory,before,video_id,expected_images=None):
    """Keep verified files that already landed when the downloader timed out."""
    output=collect_output(directory,before,video_id,expected_images)
    output['douk_timeout']=True
    output['automatic_retry']=False
    return output


def main():
    request=json.load(sys.stdin)
    video_id=str(request['video_id'])
    if not re.fullmatch(r'\d{15,25}',video_id):raise ValueError('Invalid video ID')
    expected=expected_count(request.get('expected_images'))
    directory=Path(request['directory']).resolve()
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from app.resource_storage import media_output_allowed
    if not media_output_allowed(directory,ROOT):raise ValueError('Invalid output directory')
    directory.mkdir(parents=True,exist_ok=True)
    source=ROOT/'tools/douk-5.8'
    service=ROOT/'tools/douk-service-5.8'
    if not service.exists():
        shutil.copytree(source,service,ignore=shutil.ignore_patterns('Volume'))
    volume=service/'Volume';volume.mkdir(exist_ok=True)
    with sqlite3.connect(source/'Volume/DouK-Downloader.db') as original, sqlite3.connect(volume/'DouK-Downloader.db') as private:
        original.backup(private)
        private.execute('DELETE FROM download_data')
    config=json.loads((source/'Volume/settings.json').read_text(encoding='utf-8-sig'))
    if not config.get('cookie'):raise ValueError('DouK login Cookie missing')
    config.update(root=str(directory),folder_name='douk',max_retry=0,timeout=DOUK_HTTP_TIMEOUT_SECONDS,
                  max_size=256*1024*1024,run_command='',download=True)
    (volume/'settings.json').write_text(json.dumps(config,ensure_ascii=False),encoding='utf-8')
    # No download history is copied: each media task has its own destination.
    before={p.resolve() for p in directory.rglob('*') if p.is_file()}
    try:
        subprocess.run([str(service/'DouK-Downloader.exe')],cwd=service,
            input='5\n2\n1\nhttps://www.douyin.com/video/'+video_id+'\nQ\n',
            capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=DOUK_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        print(json.dumps(collect_output(directory,before,video_id,expected)))
    except subprocess.TimeoutExpired:
        # Files that already finished downloading stay usable; completeness is
        # stated instead of pretending the whole gallery arrived.
        print(json.dumps(salvage_timeout(directory,before,video_id,expected)))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'status':'unavailable','reason':'douk_download_failed','error_class':type(exc).__name__,'automatic_retry':False}))
