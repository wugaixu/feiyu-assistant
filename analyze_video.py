import argparse
import json
from pathlib import Path
from app.video_analyzer import analyze_local_video

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Extract reference frames from an explicitly supplied local video')
    parser.add_argument('video', type=Path)
    args = parser.parse_args()
    result = analyze_local_video(args.video, Path(r'__RUNTIME_ROOT__\references'))
    print(json.dumps(result, ensure_ascii=False, indent=2))
