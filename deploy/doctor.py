"""Read-only installation checks. No login, message, download or model inference."""
import argparse,importlib.util,json,shutil
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime-dir',required=True,type=Path);a=p.parse_args();r=a.runtime_dir.resolve()
cfg=json.loads((r/'deployment.json').read_text(encoding='utf-8'))
checks={
 'configured_code':(Path(cfg['installed'])/'app/console_server.py').is_file(),
 'node_available':bool(shutil.which('node')),
 'sdk_package':(r/'node_modules/douyin-im/lib/index.js').is_file(),
 'whale_bridge_secret':(r/'whale-bridge-secret.txt').is_file(),
 'ASR_model_optional':(r/'tools/sensevoice/models/model.int8.onnx').is_file(),
 'ASR_runtime_optional':(r/'tools/sensevoice/node_modules/sherpa-onnx-node/package.json').is_file(),
 'AstrBot_bridge_optional':(r/'astrbot-bridge.json').is_file(),
 'AstrBot_reader_required_when_selected':(r/'astrbot/runtime/data/plugins/workflow_local_vision/main.py').is_file(),
 'DouK_optional':(r/'tools/douk-5.8/DouK-Downloader.exe').is_file(),
 'DouK_login_config_optional':(r/'tools/douk-5.8/Volume/settings.json').is_file(),
 'TTS_engine_optional':(r/'tools/gpt-sovits/app/api_v2.py').is_file(),
 'image_engine_optional':(r/'tools/local-image/ComfyUI/main.py').is_file(),
}
for module in ('PIL','pillow_heif','pypdf','yaml','imageio_ffmpeg','playwright'):checks['python_'+module]=importlib.util.find_spec(module) is not None
for name,ok in checks.items():print(('OK ' if ok else 'MISSING ')+name)
print('Presence is not a successful login, model inference or recipient delivery.')
