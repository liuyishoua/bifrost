import io
import os
from html import escape
import json
from pathlib import Path
from urllib.parse import urlsplit
from flask import Flask, jsonify, request, send_file
from werkzeug.exceptions import HTTPException
from .service import Service
from .media import MAX_UPLOAD_BODY
from .overview import snapshot
from .account_performance import account_performance
from .send_records import records, task_followups
from .verification import VERIFICATION_METHODS
from dy_apis.douyin_im_history import HistoryError

VERIFICATION_HOST = 'verification.localhost'


def create_app(directory=None, adapter=None, background=True):
    app = Flask(__name__, static_folder='static', static_url_path='/static')
    app.config.update(MAX_CONTENT_LENGTH=2*1024*1024, TRUSTED_HOSTS=['localhost', '127.0.0.1', '[::1]', VERIFICATION_HOST])
    s = Service(directory or Path('datas/web'), adapter=adapter, background=background)
    app.extensions['console'] = s

    @app.before_request
    def same_origin():
        # This origin permits component cookies but must never expose console APIs.
        if urlsplit(request.host_url).hostname == VERIFICATION_HOST:
            if request.path.startswith('/verification-request/'):
                verify_origin = os.environ.get('ANYDOOR_VERIFY_ORIGIN', '').rstrip('/')
                expected_origin = (verify_origin if verify_origin and request.remote_addr in ('127.0.0.1', '::1')
                                   else request.host_url.rstrip('/'))
                if (request.headers.get('Origin', expected_origin) != expected_origin or
                        request.headers.get('Sec-Fetch-Site', 'same-origin') != 'same-origin'):
                    return jsonify(error='验证请求来源无效'), 403
            elif request.method not in ('GET', 'HEAD') or request.path not in (
                    '/verification-frame', '/static/verification-frame.js'):
                return jsonify(error='记录不存在'), 404
        elif request.path.startswith('/verification-request/') or request.path in ('/verification-frame', '/static/verification-frame.html'):
            return jsonify(error='记录不存在'), 404
        if request.path.startswith('/api/') and request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('Origin')
            mounted = (request.headers.get('X-Forwarded-Prefix') == '/douyin' and
                       request.remote_addr in ('127.0.0.1', '::1'))
            portal_origin = os.environ.get('ANYDOOR_PUBLIC_ORIGIN', '').rstrip('/')
            valid_origin = (origin == portal_origin and bool(portal_origin) if mounted else
                            not origin or urlsplit(origin).netloc == request.host)
            if (request.headers.get('X-App-Request') != '1' or not request.is_json or
                    not valid_origin):
                return jsonify(error='请求来源无效，请从本机页面操作'), 403

    @app.after_request
    def headers(response):
        if response.mimetype == 'text/html' and response.status_code == 200:
            prefix = request.headers.get('X-Forwarded-Prefix', '')
            prefix = prefix if prefix == '/douyin' else ''
            tags = f'<base href="{prefix}/">'
            for name, env in (('verification-origin', 'ANYDOOR_VERIFY_ORIGIN'), ('portal-origin', 'ANYDOOR_PUBLIC_ORIGIN')):
                if name == 'verification-origin' and not prefix:
                    continue
                origin = os.environ.get(env, '')
                parsed = urlsplit(origin)
                if parsed.scheme in ('http', 'https') and parsed.netloc and not parsed.path and not parsed.query and not parsed.fragment:
                    tags += f'<meta name="{name}" content="{escape(origin, quote=True)}">'
            response.direct_passthrough = False
            html = response.get_data(as_text=True)
            html = html.replace('<head>', '<head>' + tags, 1) if '<head>' in html else html.replace('<meta', tags + '<meta', 1)
            response.set_data(html)
            response.headers.pop('ETag', None)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data: blob:; connect-src 'self'; frame-ancestors 'none'"
        if response.status_code == 200 and request.path == '/verification-frame':
            from .login import VERIFY_DOMAINS
            hosts = ' '.join('https://' + prefix + domain for domain in VERIFY_DOMAINS for prefix in ('', '*.'))
            public_origin = request.headers.get('X-Forwarded-Proto', request.scheme) + '://' + request.headers.get('X-Forwarded-Host', request.host)
            response.headers['Content-Security-Policy'] = (
                "sandbox allow-scripts allow-forms allow-popups allow-same-origin; default-src 'none'; "
                f"script-src {public_origin}/static/verification-frame.js https://lf-ucenter-web.yhgfb-cn-static.com {hosts}; "
                f"connect-src {public_origin}/verification-request/ {hosts}; frame-src {hosts}; img-src https: data: blob:; "
                f"style-src 'unsafe-inline' {hosts}; font-src {hosts} data:; frame-ancestors 'none'")
        if request.path.startswith('/api/') or 'verification' in request.path:
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(Exception)
    def error(exc):
        if isinstance(exc, HistoryError):
            return jsonify(error='历史同步未完成，已保存的消息和进度保留，请重试'), 502
        if isinstance(exc, LookupError):
            return jsonify(error='记录不存在'), 404
        if isinstance(exc, ValueError):
            return jsonify(error=str(exc)), 400
        if isinstance(exc, HTTPException):
            return jsonify(error='请求格式或地址无效'), exc.code
        # Never expose upstream exception text: it may contain login credentials.
        return jsonify(error='操作未完成，请刷新状态后重试'), 500

    def body():
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError('请求内容必须为对象')
        return data

    @app.post('/api/media')
    def media_upload():
        request.max_content_length = MAX_UPLOAD_BODY
        return s.media.upload(body())

    @app.get('/api/media/<media_id>')
    def media_file(media_id):
        return send_file(s.media.path(media_id))

    @app.get('/api/meta')
    def meta():
        return {'demo': s.adapter.demo, 'now': s.clock()}

    @app.get('/api/overview')
    def overview():
        return snapshot(s, request.args.to_dict())

    @app.get('/api/overview/accounts')
    def overview_accounts():
        return account_performance(s, request.args.to_dict())

    @app.get('/api/send-records')
    def send_records():
        return records(s, request.args.to_dict())

    @app.route('/api/accounts', methods=['GET', 'POST'])
    def accounts():
        return s.accounts.list() if request.method == 'GET' else s.accounts.add(body())

    @app.route('/api/accounts/<aid>', methods=['PATCH', 'DELETE'])
    def account_update(aid):
        data = body()
        if request.method == 'DELETE':
            return s.accounts.delete(aid)
        return s.accounts.update_cookie(aid, data)

    @app.post('/api/accounts/<aid>/check')
    def account_check(aid):
        body()
        return s.accounts.check(aid)

    @app.patch('/api/accounts/<aid>/sending')
    def account_sending(aid):
        return s.accounts.configure_sending(aid, body())

    @app.patch('/api/accounts/batch/sending')
    def account_batch_sending():
        return s.accounts.configure_batch(body())

    @app.route('/api/accounts/<aid>/qr', methods=['GET', 'POST'])
    def account_qr(aid):
        if request.method == 'GET':
            return s.accounts.get(aid)
        body()
        return s.accounts.qr(aid)

    @app.route('/api/accounts/<aid>/verification', methods=['GET', 'POST'])
    def account_verification(aid):
        if request.method == 'POST':
            return s.accounts.finish_verification(aid, body())
        return s.accounts.verification(aid)

    @app.get('/verification-frame')
    def verification_frame():
        return app.send_static_file('verification-frame.html')

    @app.route('/verification-request/<nonce>/<path:path>', methods=['GET', 'POST'])
    def verification_request(nonce, path):
        path = '/' + path
        if VERIFICATION_METHODS.get(path) != request.method:
            return jsonify(error='不支持该验证接口或请求方法'), 404
        try:
            return s.accounts.verification_request(nonce, path, request.method,
                list(request.args.items(multi=True)), request.get_data(), request.headers)
        except ValueError:
            raise
        except Exception:
            # No exception text, request data, redirects, Cookies or upstream headers.
            return jsonify(message='error', data={'description': '验证接口请求失败，请稍后重试'}), 502

    @app.get('/api/accounts/<aid>/qr-image')
    def qr_image(aid):
        import qrcode
        row = s.accounts.get(aid)
        if not row['qr_url']:
            raise LookupError('二维码尚未就绪')
        output = io.BytesIO()
        qrcode.make(row['qr_url']).save(output, format='PNG')
        output.seek(0)
        return send_file(output, mimetype='image/png', max_age=0)

    @app.post('/api/searches')
    def search_create():
        return s.search.create(body())

    @app.get('/api/searches/<sid>')
    def search_get(sid):
        return s.search.get(sid)

    @app.get('/api/searches/<sid>/results')
    def search_results(sid):
        return s.search.results(sid, request.args.to_dict())

    @app.post('/api/searches/<sid>/selection')
    def search_selection(sid):
        return s.search.selection(sid, body())

    @app.post('/api/searches/<sid>/import')
    def search_import(sid):
        data = body()
        return s.search.import_users(sid, data.get('uids'), data.get('tag_ids'))

    @app.route('/api/tags', methods=['GET', 'POST'])
    @app.route('/api/message-templates', methods=['GET', 'POST'])
    def library_list():
        kind = request.path.rsplit('/', 1)[-1]
        return s.library.list(kind) if request.method == 'GET' else s.library.save(kind, body())

    @app.route('/api/tags/<item_id>', methods=['PATCH', 'DELETE'])
    @app.route('/api/message-templates/<item_id>', methods=['PATCH', 'DELETE'])
    def library_item(item_id):
        kind = request.path.split('/')[2]
        data = body()
        return s.library.delete(kind, item_id) if request.method == 'DELETE' else s.library.save(kind, data, item_id)

    @app.post('/api/users/tags')
    def user_tags():
        return s.library.user_tags(body())

    @app.get('/api/users')
    def users():
        return s.users(request.args.to_dict())

    @app.post('/api/users/selection')
    def users_selection():
        return s.selection(body())

    @app.post('/api/users/bulk-delete')
    def users_delete():
        return s.delete_users(body().get('uids'))

    @app.post('/api/users/bulk-edit')
    def users_edit():
        return s.batch_edit(body())

    @app.patch('/api/users/<uid>')
    def user_edit(uid):
        return s.edit_user(uid, body())

    @app.get('/api/users/<uid>/history')
    def user_history(uid):
        data = s.history(uid)
        for row in data['items']:
            row['message'] = json.loads(row.pop('config'))['message']
        return data

    @app.get('/api/users/<uid>/messages')
    def user_messages(uid):
        return s.messages.list(uid, request.args.to_dict())

    @app.get('/api/users/<uid>/conversation')
    def user_conversation(uid):
        return s.conversation.list(uid, request.args.to_dict())

    @app.post('/api/users/<uid>/conversation/send')
    def user_conversation_send(uid):
        return s.conversation.send(uid, body())

    @app.post('/api/users/<uid>/messages/sync')
    def user_messages_sync(uid):
        return s.messages.sync(uid, body())

    @app.route('/api/tasks', methods=['GET', 'POST'])
    def tasks():
        return s.tasks.list(request.args.to_dict()) if request.method == 'GET' else s.tasks.create(body())

    @app.route('/api/tasks/<tid>', methods=['GET', 'PATCH', 'DELETE'])
    def task(tid):
        if request.method == 'DELETE':
            body()
            return s.tasks.delete(tid)
        return s.tasks.get(tid) if request.method == 'GET' else s.tasks.edit(tid, body())

    @app.get('/api/tasks/<tid>/recipients')
    def task_recipients(tid):
        return s.tasks.recipients(tid, request.args.to_dict())

    @app.get('/api/tasks/<tid>/replies')
    def task_replies(tid):
        return s.reply_reviews.replies(tid, request.args.to_dict())

    @app.get('/api/tasks/<tid>/followups')
    def followups(tid):
        return task_followups(s, tid, request.args.to_dict())

    @app.post('/api/tasks/<tid>/<action>')
    def task_action(tid, action):
        body()
        if action not in ('start', 'pause', 'resume'):
            raise LookupError('操作不存在')
        return getattr(s.tasks, action)(tid)

    @app.get('/api/tasks/<tid>/dashboard')
    def dashboard(tid):
        return s.tasks.dashboard(tid, request.args.get('after_event_id', 0))

    @app.get('/')
    @app.get('/overview')
    @app.get('/search')
    @app.get('/users')
    @app.get('/send-details')
    @app.get('/accounts')
    @app.get('/tasks')
    @app.get('/tasks/<tid>')
    @app.get('/tags')
    @app.get('/message-templates')
    @app.get('/tasks/<tid>/dashboard')
    def page(tid=None):
        return app.send_static_file('index.html')

    return app
