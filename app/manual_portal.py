"""Loopback-only manual Qwen reference-image portal; no messaging integration."""
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app import local_images

PORT = 8189
URL = f'http://127.0.0.1:{PORT}'
OUTPUT = Path(r'__IMAGE_OUTPUT__')
STATE = local_images.ROOT / 'manual-images'
HTML = Path(__file__).resolve().parents[1] / 'integrations/comfy_manual/portal.html'
LOCK = threading.RLock()
JOBS = {}
UPLOADS = set()
TOKEN = secrets.token_urlsafe(32)


def graph_for(data):
    prompt = data.get('prompt', '')
    refs = data.get('images', [])
    steps = data.get('steps', 8)
    resolution = data.get('resolution', 768)
    seed = data.get('seed', -1)
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 6000:
        raise ValueError('请输入1–6000字的提示词')
    if not isinstance(refs, list) or len(refs) > 16 or any(not isinstance(x, str) or x not in UPLOADS for x in refs):
        raise ValueError('参考图无效，请重新上传（最多16张）')
    if type(steps) is not int or not 1 <= steps <= 60:
        raise ValueError('步数需为1–60')
    if resolution not in (512, 768, 1024):
        raise ValueError('分辨率无效')
    if type(seed) is not int or not -1 <= seed <= 2**32 - 1:
        raise ValueError('种子无效')
    graph = local_images.workflow(prompt.strip())
    graph['4']['inputs'].pop('images', None)
    graph['4']['inputs']['resolution'] = resolution
    for index, filename in enumerate(refs, 1):
        node = str(100 + index)
        graph[node] = {'class_type': 'LoadImage', 'inputs': {'image': filename}}
        graph['4']['inputs'][f'images.image_{index}'] = [node, 0]
    # The native Qwen node returns the correctly shaped 64-channel latent,
    # matching the first reference's aspect ratio for image editing.
    graph['6']['inputs'].update(latent_image=['4', 2], steps=steps,
                              seed=secrets.randbelow(2**32) if seed == -1 else seed)
    graph.pop('5')
    # Intermediate images remain in the existing runtime temp tree. The worker
    # copies verified output bytes to OUTPUT even if the browser is closed.
    graph['8'] = {'class_type': 'PreviewImage', 'inputs': {'images': ['7', 0]}}
    return graph


def persist():
    STATE.mkdir(parents=True, exist_ok=True)
    temp = STATE / 'jobs.tmp'
    temp.write_text(json.dumps(JOBS, ensure_ascii=False), encoding='utf-8')
    temp.replace(STATE / 'jobs.json')


def visible_jobs(include_deleted=False):
    with LOCK:
        return [dict(job) for job in reversed(list(JOBS.values()))
                if include_deleted or not job.get('deleted_at')][:30]


def edit_records(data):
    """Recoverable record removal; never delete output files or active work."""
    if not isinstance(data, dict) or data.get('action') not in ('delete', 'restore'):
        raise ValueError('无效的记录操作')
    action = data['action']
    ids = data.get('ids')
    if data.get('all_finished') is True:
        if action != 'delete' or ids is not None:
            raise ValueError('无效的批量操作')
    elif not isinstance(ids, list) or not 1 <= len(ids) <= 500 or any(not isinstance(x, str) for x in ids):
        raise ValueError('请选择1–500条记录')
    with LOCK:
        selected = list(JOBS) if data.get('all_finished') is True else list(dict.fromkeys(ids))
        if any(key not in JOBS for key in selected):
            raise ValueError('记录不存在，请刷新页面')
        previous = {}
        changed = []
        skipped = 0
        for key in selected:
            job = JOBS[key]
            if job.get('state') not in ('done', 'failed', 'uncertain'):
                skipped += 1
                continue
            if bool(job.get('deleted_at')) == (action == 'delete'):
                continue
            previous[key] = dict(job)
            if action == 'delete':
                job['deleted_at'] = time.time()
            else:
                job.pop('deleted_at', None)
            changed.append(key)
        try:
            if changed:
                persist()
        except OSError:
            JOBS.update(previous)
            raise
        return {'ids': changed, 'changed': len(changed), 'skipped': skipped, 'action': action}


def request_bytes(path, data=None, content_type=None, limit=40_000_000):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}), local_images._NoRedirect())
    headers = {'Content-Type': content_type} if content_type else {}
    req = urllib.request.Request(local_images.URL + path, data=data, headers=headers)
    with op.open(req, timeout=60) as response:
        body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError('文件过大')
    return body


def complete(job):
    item = local_images.api('/history/' + job['prompt_id']).get(job['prompt_id'])
    if not item:
        return
    if item.get('status', {}).get('status_str') == 'error':
        messages = item.get('status', {}).get('messages', [])
        detail = next((m[1].get('exception_message', '') for m in messages if m[0] == 'execution_error'), '')
        job.update(state='failed', error='模型执行失败：' + detail[:1600])
        return
    images = item.get('outputs', {}).get('8', {}).get('images', [])
    if not images:
        if item.get('status', {}).get('completed'):
            job.update(state='failed', error='任务结束但没有生成图片')
        return
    OUTPUT.mkdir(parents=True, exist_ok=True)
    saved = []
    for index, image in enumerate(images):
        dest = OUTPUT / f"qwen-{job['id']}-{index + 1}.png"
        if not dest.exists():
            body = request_bytes('/view?' + urllib.parse.urlencode(image))
            if not body.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('后端返回的文件不是PNG')
            # An interrupted write cannot leave a truncated final image.
            pending = STATE / (dest.name + '.pending')
            pending.write_bytes(body)
            os.link(pending, dest)
            pending.unlink()
        saved.append(dest.name)
    job.update(state='done', files=saved, output=str(OUTPUT))


def ensure_ready(timeout=180):
    """Start the reusable local backend when it is not running; no remote host."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            local_images.api('/system_stats', timeout=2)
            return True
        except Exception:
            try:
                local_images.ensure_backend()
            except Exception:
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(1)


def worker():
    while True:
        busy = False
        with LOCK:
            before = json.dumps(JOBS, ensure_ascii=False)
            for job in JOBS.values():
                if job['state'] == 'running':
                    try:
                        complete(job)
                        job.pop('poll_error', None)
                    except Exception as exc:
                        job['poll_error'] = str(exc)[:1200]
            if json.dumps(JOBS, ensure_ascii=False) != before:
                persist()
            busy = any(job['state'] in ('submitting', 'running') for job in JOBS.values())
        # The portal stays tiny; the heavy backend is released once idle.
        try:
            if busy:
                local_images.touch('manual-job')
            elif local_images.idle_expired():
                local_images.stop_backend('portal-idle')
        except Exception:
            pass
        time.sleep(2)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{PORT}', f'localhost:{PORT}')

    def do_GET(self):
        if not self.valid_host():
            return self.send(403, {'error': '仅限本机访问'})
        path = urllib.parse.urlsplit(self.path).path
        if path == '/health':
            return self.send(200, {'service': 'manual-qwen-portal', 'version': 2, 'record_delete_version': 1})
        if path == '/':
            return self.send(200, HTML.read_text(encoding='utf-8').replace('__TOKEN__', TOKEN).encode('utf-8'), 'text/html; charset=utf-8')
        if path == '/api/jobs':
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            return self.send(200, {'jobs': visible_jobs(query.get('include_deleted') == ['1']),
                                   'output': str(OUTPUT), 'record_delete_version': 1})
        if path.startswith('/image/'):
            name = urllib.parse.unquote(path[len('/image/'):])
            with LOCK:
                allowed = {f for j in JOBS.values() for f in j.get('files', [])}
            if name not in allowed or Path(name).name != name:
                return self.send(404, {'error': '图片不存在'})
            return self.send(200, (OUTPUT / name).read_bytes(), 'image/png')
        return self.send(404, {'error': '页面不存在'})

    def do_POST(self):
        if not self.valid_host() or self.headers.get('X-Manual-Token') != TOKEN:
            return self.send(403, {'error': '请从本机页面操作'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 25_000_000:
                raise ValueError('单张图片需小于25MB')
            body = self.rfile.read(length)
            if self.path == '/api/records':
                return self.send(200, edit_records(json.loads(body)))
            if self.path == '/api/upload':
                # The backend idles out; wake it before the first upload too,
                # otherwise uploading a reference image fails with 10061.
                if not ensure_ready():
                    raise ValueError('本地生图后端启动失败，请查看 comfyui.log')
                result = json.loads(request_bytes('/upload/image', body, self.headers.get('Content-Type')))
                name = '/'.join(filter(None, (result.get('subfolder'), result['name'])))
                with LOCK:
                    UPLOADS.add(name)
                return self.send(200, {'name': name})
            if self.path == '/api/generate':
                data = json.loads(body)
                if not ensure_ready():
                    raise ValueError('本地生图后端启动失败，请查看 comfyui.log')
                with LOCK:
                    if sum(j['state'] == 'running' for j in JOBS.values()) >= 3:
                        raise ValueError('已有3个生成任务，请等待完成')
                    graph = graph_for(data)
                    key = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:10]
                    job = {'id': key, 'state': 'submitting', 'prompt': data['prompt'], 'references': len(data.get('images', [])), 'seed': graph['6']['inputs']['seed']}
                    JOBS[key] = job
                    persist()
                    try:
                        response = local_images.api('/prompt', {'prompt': graph, 'client_id': 'manual-portal-' + key}, timeout=30)
                        if not response.get('prompt_id'):
                            raise ValueError(str(response))
                        job.update(state='running', prompt_id=response['prompt_id'])
                        local_images.touch('manual-submit')
                    except Exception as exc:
                        job.update(state='uncertain', error='提交未确认，请检查ComfyUI队列后再决定是否重新生成：' + str(exc)[:1200])
                    persist()
                    return self.send(200, job)
            return self.send(404, {'error': '接口不存在'})
        except Exception as exc:
            return self.send(400, {'error': str(exc)[:1600]})


def main():
    STATE.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if (STATE / 'jobs.json').exists():
        JOBS.update(json.loads((STATE / 'jobs.json').read_text(encoding='utf-8')))
        for job in JOBS.values():
            if job['state'] == 'submitting':
                job.update(state='uncertain', error='提交时服务曾退出，请检查ComfyUI队列；不会自动重提')
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    threading.Thread(target=worker, daemon=True).start()
    server.serve_forever()


if __name__ == '__main__':
    main()
