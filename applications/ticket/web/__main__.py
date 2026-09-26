"""Run with python -m web. Binds only to loopback."""

import argparse
import json
import mimetypes
import secrets
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from ticket_app.configuration import AppError
from .service import Workbench, clean


STATIC = Path(__file__).parent / 'static'


def make_server(workbench, port=8767):
    csrf = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send(self, body, content_type='application/json', status=200):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def allowed(self):
            return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

        def do_GET(self):
            if not self.allowed():
                return self.send({'error': '仅允许本机访问'}, status=403)
            path = urlparse(self.path).path
            if path == '/api/state':
                return self.send(workbench.state())
            if path == '/api/stations':
                return self.send(list(workbench.stations))
            parts = path.strip('/').split('/')
            if len(parts) == 4 and parts[:2] == ['api', 'accounts'] and parts[3] == 'session-history':
                try:
                    workbench.account(parts[2])
                    query = parse_qs(urlparse(self.path).query)
                    before = int(query['before'][0]) if 'before' in query else None
                    return self.send(workbench.store.session_history(parts[2], before=before))
                except (AppError, ValueError):
                    return self.send({'error': '账号不存在或分页参数无效'}, status=400)
            if len(parts) == 4 and parts[:2] == ['api', 'accounts'] and parts[3] == 'login':
                try:
                    return self.send(workbench.account(parts[2]).login.copy())
                except AppError as exc:
                    return self.send({'error': clean(exc)}, status=404)
            files = {'/': 'index.html', '/static/style.css': 'style.css', '/static/app.js': 'app.js', '/static/rules.js': 'rules.js'}
            if path not in files:
                return self.send({'error': '未找到页面'}, status=404)
            file = STATIC / files[path]
            body = file.read_bytes()
            if path == '/':
                body = body.replace(b'__CSRF__', csrf.encode())
            self.send(body, (mimetypes.guess_type(str(file))[0] or 'text/plain') + '; charset=utf-8')

        def do_POST(self):
            if not self.allowed() or self.headers.get('X-Workbench-Token') != csrf:
                return self.send({'error': '页面已失效，请刷新后操作'}, status=403)
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size < 65536:
                    raise AppError('请求过大或为空')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise AppError('请求必须是对象')
                path = urlparse(self.path).path.strip('/').split('/')
                if path == ['api', 'accounts']:
                    result = workbench.add_account(data.get('name', ''))
                elif path == ['api', 'query']:
                    # Query only accepts route/account. Booking configuration cannot leak into it.
                    result = workbench.query({k: data[k] for k in ('account_id', 'from_station', 'to_station', 'train_date') if k in data})
                elif path == ['api', 'prices']:
                    result = workbench.prices(data)
                elif path == ['api', 'sale-plan']:
                    result = workbench.sale_plan(data)
                elif path == ['api', 'train-options']:
                    result = workbench.train_options(data)
                elif path == ['api', 'tasks']:
                    result = workbench.create_task(data)
                elif len(path) == 4 and path[:2] == ['api', 'accounts']:
                    key, action = path[2:]
                    if action == 'login':
                        result = workbench.login(key)
                    elif action == 'cancel-login':
                        result = workbench.cancel_login(key)
                    elif action == 'passengers':
                        result = workbench.passengers(key)
                    else:
                        result = workbench.change_account(key, action, data)
                elif len(path) == 4 and path[:2] == ['api', 'tasks'] and path[3] in {'start', 'stop', 'delete', 'edit'}:
                    if path[3] == 'edit':
                        result = workbench.edit_task(path[2], data)
                    else:
                        result = getattr(workbench, 'delete_task' if path[3] == 'delete' else path[3])(path[2])
                else:
                    return self.send({'error': '接口不存在'}, status=404)
                self.send(result)
            except (AppError, ValueError, TypeError, KeyError) as exc:
                self.send({'error': clean(exc)}, status=400)
            except Exception:
                self.send({'error': '操作失败，请检查登录和网络状态后重试'}, status=500)

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description='票务工作台（仅本机）')
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).resolve().parents[1] / '.runtime_web')
    args = parser.parse_args()
    workbench = Workbench(args.data_dir)
    server = make_server(workbench, args.port)
    workbench.start_scheduler()
    def stop(*_):
        workbench.shutdown()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f'票务工作台已启动：http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    finally:
        workbench.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
