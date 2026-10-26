import json
from app.codex_runner import run_smoke

if __name__ == '__main__':
    print(json.dumps(run_smoke(r'__RUNTIME_ROOT__'), ensure_ascii=True, indent=2))
