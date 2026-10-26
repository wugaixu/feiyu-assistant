"""Read-only reuse of DSH's installed offline speech runtime and model."""
import json
import subprocess
from pathlib import Path

DSH = Path(r'__DSH_RESOURCES__')
NODE = DSH/'runtime/primary-runtime/dependencies/node/bin/node.exe'
MODULE = DSH/'app/dsh/node_modules/sherpa-onnx-node'
MODELS = Path(r'__DSH_HOME__\speech-to-text\sensevoice\models')


def available():
    return all(p.exists() for p in (NODE,MODULE/'package.json',MODELS/'sensevoice-onnx/model.int8.onnx',MODELS/'sensevoice-onnx/tokens.txt',MODELS/'silero/silero_vad.onnx'))


def transcribe(wav, output):
    if not available():
        raise ValueError('Installed DSH SenseVoice runtime/model missing; no automatic download or install')
    result = subprocess.run([str(NODE),str(Path(__file__).with_name('transcribe_sensevoice.cjs')),
                             str(MODULE),str(MODELS/'sensevoice-onnx'),str(MODELS/'silero/silero_vad.onnx'),
                             str(wav),str(output)],capture_output=True,timeout=60,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode or not Path(output).exists():
        raise ValueError('Local SenseVoice recognition failed: '+result.stderr.decode('utf-8','replace')[-1000:])
    return json.loads(Path(output).read_text(encoding='utf-8'))
