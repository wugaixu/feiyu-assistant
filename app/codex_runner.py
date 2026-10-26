"""Bounded, fixed, read-only CLI diagnostic. Not a general remote task executor."""
import json
import shutil
import subprocess
from pathlib import Path


def run_smoke(runtime_root, timeout=120):
    executable = shutil.which('codex')
    if not executable:
        return {'status': 'unavailable', 'reason': 'Codex CLI not found'}
    root = Path(runtime_root) / 'codex-smoke'
    root.mkdir(parents=True, exist_ok=True)
    lock = root / 'running.lock'
    try:
        handle = lock.open('x')
    except FileExistsError:
        return {'status': 'blocked', 'reason': 'Another diagnostic or an unrecovered lock exists'}
    try:
        with handle:
            token = 'DOUYIN_LOCAL_SMOKE_20261023'
            (root / 'sample.txt').write_text(token + '\n', encoding='ascii')
            final = root / 'result.txt'
            # Avoid mistaking an old result for the current run's success.
            final.write_text('', encoding='utf-8')
            argv = [executable, 'exec', '--ignore-user-config', '--ignore-rules',
                    '--ephemeral', '--sandbox', 'read-only', '--cd', str(root),
                    '--skip-git-repo-check', '--json', '--output-last-message', str(final), '-']
            with (root / 'events.jsonl').open('w', encoding='utf-8') as output, (root / 'stderr.log').open('w', encoding='utf-8') as errors:
                process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=output,
                                           stderr=errors, shell=False, text=True, encoding='utf-8')
                try:
                    process.communicate('Read sample.txt in the working directory and return only its exact one-line content. Do not edit files, use network, or access other directories.', timeout=timeout)
                    actual = final.read_text(encoding='utf-8').strip()
                    result = {'status': 'passed' if process.returncode == 0 and actual == token else 'failed',
                              'returncode': process.returncode, 'result': actual}
                except subprocess.TimeoutExpired:
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
                    process.wait(timeout=10)
                    result = {'status': 'timeout'}
            (root / 'status.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            return result
    finally:
        lock.unlink(missing_ok=True)
