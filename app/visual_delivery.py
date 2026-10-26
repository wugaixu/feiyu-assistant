"""Verified local visual inputs shared by file-reading and native-image routes."""
import hashlib
from pathlib import Path

MAX_IMAGES=10
MAX_TOTAL=20*1024*1024

def select_files(records):
    from app import media_formats as formats
    from app.resource_storage import resource_dirs
    roots=resource_dirs('media')+resource_dirs('analysis')
    output=[]
    for record in records:
        analysis=record.get('shared_video_analysis') or {}
        if record.get('kind')=='image' or record.get('media_type')=='image_gallery':
            candidates=[f.get('path') for f in record.get('files',[]) if f.get('status')=='saved']
        else:candidates=[analysis.get('contact_sheet'),analysis.get('subtitle_strip')]
        for name in candidates:
            if not name:continue
            path=Path(name).resolve()
            if not path.is_file() or not any(path.is_relative_to(r.resolve()) for r in roots):continue
            if path.suffix.lower() not in formats.OUTBOUND_EXTENSIONS:continue
            if path not in output:output.append(path)
    return output


def inputs(records,limit=MAX_IMAGES,max_total=MAX_TOTAL):
    from PIL import Image
    hashes={str(Path(f['path']).resolve()):f.get('sha256') for r in records for f in r.get('files',[]) if f.get('path')}
    result=[];total=0
    for path in select_files(records):
        try:
            size=path.stat().st_size
            if not 0<size<=10*1024*1024 or (max_total is not None and total+size>max_total):continue
            raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
            if hashes.get(str(path)) and hashes[str(path)]!=digest:continue
            with Image.open(path) as image:
                mime={'PNG':'image/png','JPEG':'image/jpeg','GIF':'image/gif','WEBP':'image/webp'}.get(image.format)
                if not mime or image.width*image.height>50_000_000:continue
                image.verify()
            result.append({'path':str(path),'sha256':digest,'mediaType':mime});total+=size
        except (OSError,ValueError,Image.DecompressionBombError):continue
    return result if limit is None else result[:limit]
