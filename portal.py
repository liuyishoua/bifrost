"""Bifrost link portal with independent account management."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit, urlencode
import hashlib
import json

from flask import Flask, abort, g, make_response, redirect, render_template, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash
from waitress import serve

from link_catalog import load_links, DIRECTORY_GROUPS, CATEGORY_ALIASES
from app_visuals import app_visual
from ops_identity import COOKIE, IdentityClient, IdentityUnavailable, IdentityRejected

USER_RE = re.compile(r"[a-z0-9_-]{3,32}\Z")
def password_hash(password):
    return generate_password_hash(password, method="pbkdf2:sha256:600000")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
 password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','user')),
 status TEXT NOT NULL CHECK(status IN ('pending','active','disabled')),
 must_change INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS grants (user_id INTEGER NOT NULL REFERENCES users(id), app_id TEXT NOT NULL,
 PRIMARY KEY(user_id,app_id));
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
 target TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS attempts (kind TEXT NOT NULL, key TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS attempts_lookup ON attempts(kind,key,created_at);
CREATE TABLE IF NOT EXISTS identity_links (
 identity_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id));
"""



def create_app(runtime=None, links_path=None):
    runtime = Path(runtime or os.environ.get("BIFROST_DATA_DIR") or os.environ.get("ANYDOOR_DATA_DIR") or Path(__file__).parent / ".runtime").resolve()
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(runtime, 0o700)
    database = runtime / "portal.sqlite3"
    secret_path = runtime / "csrf.key"
    try:
        fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as secret_file:
            secret_file.write(secrets.token_bytes(32))
    csrf_signer = URLSafeTimedSerializer(secret_path.read_bytes(), salt="portal-form-v1")
    with sqlite3.connect(database) as db:
        db.executescript(SCHEMA)
    os.chmod(database, 0o600)
    portal_css = (Path(__file__).parent / "static" / "portal.css").read_text(encoding="utf-8")
    app = Flask(__name__)
    identity = IdentityClient(os.environ["OPS_IDENTITY_URL"]) if os.environ.get("OPS_IDENTITY_URL") else None
    identity_login = "https://d.58nft.net:9461/login?return=bifrost"
    app.config["MAX_CONTENT_LENGTH"] = 65536
    public_origin = (os.environ.get("BIFROST_PUBLIC_ORIGIN") or os.environ.get("ANYDOOR_PUBLIC_ORIGIN") or "http://127.0.0.1:8080").rstrip("/")
    if urlsplit(public_origin).scheme not in ("http", "https"):
        raise ValueError("BIFROST_PUBLIC_ORIGIN must be an HTTP origin")
    secure_cookie = urlsplit(public_origin).scheme == "https"
    catalog = Path(links_path or os.environ.get("BIFROST_LINKS_FILE") or Path(__file__).parent / "links.json")
    load_links(catalog)

    def apps():
        return {item.id: item for item in load_links(catalog)}

    @contextmanager
    def db():
        conn = sqlite3.connect(database, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def audit(conn, actor, action, target):
        conn.execute("INSERT INTO audit(actor,action,target,created_at) VALUES(?,?,?,?)",
                     (actor, action, target, time.time()))

    def current():
        if hasattr(g, "user"):
            return g.user
        if identity:
            remote = identity.verify(request.cookies.get(COOKIE, ""))
            g.user = None
            if remote:
                with db() as conn:
                    row = conn.execute("SELECT u.* FROM users u JOIN identity_links i ON i.user_id=u.id WHERE i.identity_id=?",
                                       (remote["id"],)).fetchone()
                    if row is None:
                        # Never grant legacy roles just because a username matches.
                        if conn.execute("SELECT 1 FROM users WHERE username=?", (remote["username"],)).fetchone():
                            abort(403, "账号尚未完成统一身份关联，请联系管理员")
                        uid = conn.execute("INSERT INTO users(username,password_hash,role,status,created_at) VALUES(?,'!central-identity','user','active',?)",
                                           (remote["username"], time.time())).lastrowid
                        conn.execute("INSERT INTO identity_links VALUES(?,?)", (remote["id"], uid))
                        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
                    if remote.get("status", row["status"]) == "active":
                        g.user = dict(row)
                        g.user['username'] = remote['username']
                        g.user["must_change"] = 0
                        g.user["role"] = remote.get("role", "user")
                        g.user['is_superadmin'] = bool(remote.get('is_superadmin', False))
                        g.user["app_permissions"] = remote.get("app_permissions", {})
                        g.user["identity_id"] = remote["id"]
                    else:
                        abort(403, "此账号的门户访问尚未启用，请联系管理员")
            return g.user
        token = request.cookies.get("portal_session", "")
        with db() as conn:
            g.user = conn.execute("""SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
                WHERE s.token_hash=? AND s.expires_at>? AND u.status='active'""",
                (digest(token), time.time())).fetchone() if token else None
        return g.user

    def csrf():
        if not getattr(g, "csrf_token", None):
            session = request.cookies.get(COOKIE if identity else "portal_session", "")
            binding = digest(session) if session else "anonymous"
            g.csrf_token = csrf_signer.dumps({"binding": binding, "nonce": secrets.token_urlsafe(16)})
        return g.csrf_token

    def form_csrf():
        return f'<input type=hidden name=csrf value="{escape(csrf())}">'

    def session_state(user):
        if not user:
            return {'authenticated': False}
        profile = dict(user)
        value = [profile.get('identity_id', profile['id']), profile['username'], profile['role'], bool(profile.get('is_superadmin')),
                 sorted((key, item.get('state')) for key, item in profile.get('app_permissions', {}).items())]
        return {'authenticated': True, 'stamp': hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()}

    @app.get('/api/session-state')
    def check_session_state():
        user = current()
        response = make_response(session_state(user), 200 if user else 401)
        response.headers['Cache-Control'] = 'no-store'
        return response

    def page(title, body, message="", subtitle="", feedback="error"):
        response = make_response(render_template("shell.html", title=title, body=body, message=message,
                                                        subtitle=subtitle, user=current(), csrf_token=csrf(),
                                                        portal_css=portal_css, feedback=feedback, session_state=session_state(current())))
        response.headers["Cache-Control"] = "no-store"
        return response

    def allowed_next(value):
        return value if value == "/" else "/"

    def require_user():
        user = current()
        if not user:
            if identity and request.path in ('/admin', '/permissions'):
                return None, redirect('/login?' + urlencode({'next': request.path}))
            return None, redirect("/login")
        return user, None

    def require_admin():
        user, response = require_user()
        if response:
            return None, response
        if user["role"] != "admin":
            abort(403)
        return user, None

    @app.before_request
    def protect_forms():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            submitted = request.form.get("csrf", "")
            try:
                token = csrf_signer.loads(submitted, max_age=12 * 3600)
            except (BadSignature, SignatureExpired):
                token = {}
            session = request.cookies.get(COOKIE if identity else "portal_session", "")
            binding = digest(session) if session else "anonymous"
            origin = request.headers.get("Origin")
            fetch_site = request.headers.get("Sec-Fetch-Site")
            same_source = (origin == public_origin or
                           origin in (None, "null") and fetch_site == "same-origin")
            if not same_source:
                app.logger.warning("form_source_rejected path=%s origin=%s fetch_site=%s", request.path, origin, fetch_site)
                return page("表单验证失败", "<a href='/register'>重新打开注册页</a> · <a href='/login'>重新打开登录页</a>",
                            "请求来源无效，请从门户页面重新提交"), 403
            if not token or token.get("binding") != binding:
                app.logger.info("form_token_expired path=%s", request.path)
                destination = ("/register" if request.path == "/register" else
                               "/login" if request.path == "/login" else
                               "/settings" if request.path == "/settings" else
                               "/admin" if request.path.startswith("/admin/") else "/")
                return redirect(destination + "?expired=1", code=303)

    def issue(response, user_id, remember=False):
        token = secrets.token_urlsafe(32)
        ttl = 30 * 86400 if remember else 12 * 3600
        with db() as conn:
            conn.execute("INSERT INTO sessions VALUES(?,?,?)", (digest(token), user_id, time.time() + ttl))
        response.set_cookie("portal_session", token, httponly=True, samesite="Lax", secure=secure_cookie,
                            max_age=ttl if remember else None)
        return response

    def limited(conn, kind, key, limit, window):
        now = time.time()
        conn.execute("DELETE FROM attempts WHERE created_at<?", (now - 86400,))
        count = conn.execute("SELECT count(*) FROM attempts WHERE kind=? AND key=? AND created_at>?",
                             (kind, key, now - window)).fetchone()[0]
        conn.execute("INSERT INTO attempts VALUES(?,?,?)", (kind, key, now))
        return count >= limit

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if identity:
            return redirect(identity_login + "&mode=register", code=303)
        if request.method == "POST":
            name = request.form.get("username", "").strip().lower()
            password = request.form.get("password", "")
            with db() as conn:
                blocked = limited(conn, "register", request.remote_addr or "", 5, 3600)
                if blocked:
                    return page("注册", register_form(), "注册过于频繁，请稍后再试"), 429
                if not USER_RE.fullmatch(name) or not 1 <= len(password) <= 128:
                    return page("注册", register_form(), "账号或密码格式不符合要求"), 400
                try:
                    conn.execute("INSERT INTO users(username,password_hash,role,status,created_at) VALUES(?,?,'user','pending',?)",
                                 (name, password_hash(password), time.time()))
                    audit(conn, name, "register", name)
                except sqlite3.IntegrityError:
                    return page("注册", register_form(), "账号名已存在"), 409
            return page("待审批", "<div class='panel auth-panel success-panel'><span class=success-symbol aria-hidden=true>✓</span><h2>注册申请已提交</h2><p>请等待管理员审批并设置可见链接。</p><a class=button href='/login'>去登录</a></div>")
        return page("注册", register_form(), "页面已更新，请重新填写" if request.args.get("expired") else "")

    def register_form():
        return f"<form class='panel auth-panel' method=post>{form_csrf()}<p class=form-intro>创建账号后，管理员审批后即可查看全部项目。</p><label>账号<input name=username type=text autocomplete=username required minlength=3 maxlength=32 placeholder='3–32 位小写字母、数字或下划线'></label><label>密码<input name=password type=password autocomplete=new-password required maxlength=128 placeholder='请输入密码'></label><button class=full-button>创建账号</button><p class=form-foot>已有账号？<a href='/login'>直接登录</a></p></form>"

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if identity:
            if current():
                target = request.args.get('next', '/')
                return redirect(target if target in ('/', '/permissions', '/admin') else '/', code=303)
            target = request.args.get('next', '/')
            destination = {'/permissions': 'permissions', '/admin': 'portal-admin'}.get(target, 'bifrost')
            query = {'return': destination}
            if destination == 'permissions' and request.args.get('app') in apps():
                query['app'] = request.args['app']
            return redirect('https://d.58nft.net:9461/login?' + urlencode(query), code=303)
        target = allowed_next(request.values.get("next", "/"))
        if request.method == "POST":
            name = request.form.get("username", "").strip().lower()
            with db() as conn:
                blocked = limited(conn, "login", f"{request.remote_addr}:{name}", 8, 900)
                row = conn.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()
            if blocked:
                return page("登录", login_form(target), "尝试过于频繁，请稍后再试"), 429
            if not row or not check_password_hash(row["password_hash"], request.form.get("password", "")):
                return page("登录", login_form(target), "账号或密码错误"), 401
            if row["status"] != "active":
                return page("登录", login_form(target), "账号待审批或已停用"), 403
            response = redirect("/settings" if row["must_change"] else target)
            return issue(response, row["id"], request.form.get("remember") == "1")
        return page("登录", login_form(target), "页面已更新，请重新填写" if request.args.get("expired") else "")

    def login_form(target):
        return f"<form class='panel auth-panel' method=post>{form_csrf()}<input type=hidden name=next value='{escape(target)}'><p class=form-intro>登录后查看项目列表。</p><label>账号<input name=username type=text autocomplete=username required placeholder='请输入账号'></label><label>密码<input name=password type=password autocomplete=current-password required placeholder='请输入密码'></label><label class=check-label><input name=remember type=checkbox value=1> 保持登录 30 天</label><button class=full-button>进入工作空间 <span aria-hidden=true>↗</span></button><p class=form-foot>还没有账号？<a href='/register'>申请注册</a></p></form>"

    @app.post("/logout")
    def logout():
        if identity:
            token = request.cookies.get(COOKIE, "")
            if token:
                identity.call("logout", token, {})
            response = redirect("/login", code=303)
            response.delete_cookie(COOKIE, path="/", secure=secure_cookie, httponly=True, samesite="Strict")
            response.delete_cookie("portal_session")
            return response
        token = request.cookies.get("portal_session", "")
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (digest(token),))
        response = redirect("/login")
        response.delete_cookie("portal_session")
        return response

    @app.get("/")
    def home():
        user, response = require_user()
        if response:
            return response
        if user["must_change"]:
            return redirect("/settings")
        links = list(apps().values())
        permissions = user.get("app_permissions", {}) if isinstance(user, dict) else {}
        for item in links:
            permission = permissions.get(item.id)
            item.access_state = permission.get("state", "locked") if permission else "available"
            item.access_url = ("/permissions?app=" + item.id
                               if item.access_state not in ("available", "independent") else item.url)
            item.access_label = {"locked": "申请使用", "pending": "审批中", "rejected": "查看审批结果", "revoked": "重新申请", "owner": "仅管理员", "independent": "独立登录"}.get(item.access_state, "打开")
        categories = list(dict.fromkeys(item.category for item in links))
        categories = [label for _, label, _ in DIRECTORY_GROUPS if label in categories] + [item for item in categories if item not in {label for _, label, _ in DIRECTORY_GROUPS}]
        aliases = {old: new for old, new in CATEGORY_ALIASES.items() if old not in categories and (not new or new in categories)}
        category_counts = {label: sum(item.category == label for item in links) for label in categories}
        query = request.args.get("q", "").strip()
        category = "" if query else request.args.get("category", "")
        category = aliases.get(category, category)
        view = request.args.get("view", "flat" if category else "grouped")
        if view not in ("grouped", "flat"):
            view = "grouped"
        group_labels = {key: label for key, label, _ in DIRECTORY_GROUPS}
        for index, item in enumerate(links):
            item.index = index
            item.search = f"{item.id} {item.name} {item.description} {item.category} {group_labels[item.group]}".lower()
        visible = [item for item in links if (not category or item.category == category)
                   and (not query or query.lower() in item.search)]
        groups = [dict(key=key, label=label, description=description,
                       links=[item for item in links if item.group == key],
                       visible=[item for item in visible if item.group == key])
                  for key, label, description in DIRECTORY_GROUPS]
        body = render_template("links.html", links=visible, all_links=links, categories=categories,
                               query=query, category=category, total=len(links), app_visual=app_visual,
                               groups=groups, view=view, category_counts=category_counts, category_aliases=aliases, favorite_key="bifrost-favorites:" + str(user.get("identity_id", user["id"]) if isinstance(user, dict) else user["id"]))
        return page("我的导航", body, subtitle="应用、工具与常用资源，一处直达。")

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        user, response = require_user()
        if response:
            return response
        message = "使用临时密码登录后，请先修改密码。" if user["must_change"] else ""
        if request.method == "POST":
            old, new = request.form.get("old", ""), request.form.get("new", "")
            if identity:
                result, cookies = identity.call("password", request.cookies.get(COOKIE, ""),
                                                {"old_password": old, "new_password": new})
                if result is not None:
                    response = redirect("/settings?changed=1", code=303)
                    for cookie in cookies:
                        response.headers.add("Set-Cookie", cookie)
                    return response
                message = "当前密码错误或新密码长度不符合要求，请重试"
            elif not check_password_hash(user["password_hash"], old) or not 1 <= len(new) <= 128:
                message = "旧密码错误或新密码长度不符合要求"
            else:
                with db() as conn:
                    conn.execute("UPDATE users SET password_hash=?,must_change=0 WHERE id=?",
                                 (password_hash(new), user["id"]))
                    conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
                    audit(conn, user["username"], "password_change", user["username"])
                response = issue(redirect("/"), user["id"])
                return response
        if identity and request.args.get("changed"):
            message = "统一密码已修改，请在其他工作台重新登录。"
        body = render_template('settings.html', profile=user, unified=bool(identity), form_csrf=form_csrf)
        return page('个人设置', body, message, subtitle='管理你的账号与登录密码。',
                    feedback='success' if identity and request.args.get('changed') else 'error')


    @app.get("/admin")
    def admin():
        actor, response = require_admin()
        if response:
            return response
        if identity:
            return central_admin()
        with db() as conn:
            users = conn.execute("SELECT id,username,role,status,must_change FROM users ORDER BY id DESC").fetchall()
        cards = []
        for user in users:
            state_label = {"pending": "待审批", "active": "已启用", "disabled": "已禁用"}[user["status"]]
            controls = f"<form class=user-form method=post action='/admin/user/{user['id']}'>{form_csrf()}<div class=user-fields><label>账号状态<select name=status><option value=pending {'selected' if user['status']=='pending' else ''}>待审批</option><option value=active {'selected' if user['status']=='active' else ''}>启用</option><option value=disabled {'selected' if user['status']=='disabled' else ''}>禁用</option></select></label></div><button>保存设置</button></form>"
            if not identity:
                controls += f"<form class=reset-form method=post action='/admin/reset/{user['id']}'>{form_csrf()}<label>重置密码<input type=password name=password autocomplete=new-password maxlength=128 required placeholder='输入临时密码'></label><button class=quiet>重置</button></form>"
            cards.append(f"<article class='card user-card'><div class=user-card-heading><div><h2>{escape(user['username'])}</h2><span class=role-label>{'管理员' if user['role']=='admin' else '普通用户'}</span></div><span class='status status-{escape(user['status'])}'><span class=status-dot></span>{state_label}</span></div>{controls}</article>")
        body = "<div class=user-grid>" + "".join(cards) + "</div>"
        return page("账号管理", body, subtitle="审批与管理账号。所有已启用账号默认看到全部项目，目标服务自行管理访问权限。")

    def central_access(path, data=None, method='GET'):
        return identity.access(path, request.cookies.get(COOKIE, ''), data, method)

    @app.get('/permissions')
    def permissions():
        user, response = require_user()
        if response:
            return redirect('/login?' + urlencode({'next': '/permissions', 'app': request.args.get('app', '')}))
        if not identity:
            return redirect('/')
        state = request.args.get('state', 'all')
        if state not in ('all', 'available', 'pending', 'requestable'):
            state = 'all'
        data = central_access('me')
        query = request.args.get('q', '').strip()
        for item in data['apps']:
            item['state_label'] = {'available': '已开通', 'locked': '需申请', 'pending': '审批中', 'rejected': '未通过', 'revoked': '已收回', 'independent': '独立登录', 'owner': '仅管理员'}.get(item['state'], item['state'])
            item['can_apply'] = item['requestable'] and item['state'] in ('locked', 'rejected', 'revoked')
        visible = [item for item in data['apps'] if (state == 'all' or state == 'requestable' and item['can_apply'] or item['state'] == state)
                   and (not query or query.lower() in (item['name'] + item['id']).lower())]
        counts = {'all': len(data['apps']), 'available': sum(item['state'] == 'available' for item in data['apps']), 'pending': sum(item['state'] == 'pending' for item in data['apps']), 'requestable': sum(item['can_apply'] for item in data['apps'])}
        body = render_template('permissions.html', applications=data['apps'], visible_ids={item['id'] for item in visible}, app_visual=app_visual, counts=counts, selected=request.args.get('app', ''),
                               state=state, query=query, form_csrf=form_csrf)
        return page('我的权限', body, message=request.args.get('message', ''), subtitle='查看已开通的应用，按需申请使用权限。', feedback='success')

    @app.post('/permissions/apply/<app_id>')
    def apply_permission(app_id):
        _, response = require_user()
        if response:
            return response
        if not identity:
            abort(404)
        central_access('apps/' + app_id + '/request', {'reason': request.form.get('reason', ''),
                       'acknowledge_shared': request.form.get('ack') == 'on'}, 'POST')
        return redirect('/permissions?' + urlencode({'app': app_id, 'message': '申请已提交，可在这里查看审批结果。'}), code=303)

    def central_admin(message='', status=200):
        data = central_access('admin')
        catalog = central_access('me')['apps']
        for member in data['users']:
            member['granted'] = set(member['apps'])
            member['permission_count'] = sum(1 for item in catalog if item['policy'] == 'approval' and item['id'] in member['granted'])
        tab = request.args.get('tab', 'users')
        if tab not in ('users', 'requests', 'audit'):
            tab = 'users'
        pending = [row for row in data['requests'] if row['status'] == 'pending']
        body = render_template('admin_access.html', members=data['users'], applications=catalog,
                               pending=pending, audit_rows=data['audit'], active_tab=tab,
                               form_csrf=form_csrf, actor=current())
        return page('用户与权限', body, message=message or request.args.get('message', ''),
                    subtitle='先管理用户，再按需打开权限设置。', feedback='error' if status >= 400 else 'success'), status

    @app.post('/admin/accounts/<int:user_id>')
    def manage_account(user_id):
        _, response = require_admin()
        if response:
            return response
        if not identity:
            abort(404)
        intent = request.form.get('intent', 'combined')
        if intent not in ('account', 'permissions', 'combined'):
            abort(400)
        payload = {'revision': request.form.get('revision')}
        if intent in ('account', 'combined'):
            payload.update(role=request.form.get('role'), status=request.form.get('status'))
        if intent == 'permissions' or intent == 'combined' and payload['role'] == 'user':
            payload['apps'] = request.form.getlist('apps')
        try:
            central_access('admin/users/' + str(user_id), payload, 'POST')
        except IdentityRejected as exc:
            return central_admin(exc.message, exc.status)
        if user_id == current().get('identity_id'):
            return redirect('/login?next=/admin', code=303)
        message = '应用权限已保存。' if intent == 'permissions' else '账号设置已保存。'
        return redirect('/admin?' + urlencode({'message': message}), code=303)

    @app.post('/admin/accounts/<int:user_id>/delete')
    def delete_central_account(user_id):
        _, response = require_admin()
        if response:
            return response
        if not identity:
            abort(404)
        try:
            central_access('admin/users/' + str(user_id), {'revision': request.form.get('revision'),
                           'username': request.form.get('username', '')}, 'DELETE')
        except IdentityRejected as exc:
            return central_admin(exc.message, exc.status)
        return redirect('/admin?' + urlencode({'message': '用户已删除，登录和应用授权已撤销。'}), code=303)

    @app.post('/admin/accounts/<int:user_id>/credentials')
    def edit_central_credentials(user_id):
        _, response = require_admin()
        if response:
            return response
        if not identity:
            abort(404)
        payload = {'revision': request.form.get('revision'), 'username': request.form.get('username', '')}
        if request.form.get('new_password'):
            payload['new_password'] = request.form['new_password']
        try:
            central_access(f'admin/users/{user_id}/credentials', payload, 'POST')
        except IdentityRejected as exc:
            return central_admin(exc.message, exc.status)
        return redirect('/admin?' + urlencode({'message': '账号信息已更新，该用户需使用新信息重新登录。'}), code=303)

    @app.post('/admin/accounts/<int:user_id>/password/reveal')
    def reveal_central_password(user_id):
        actor = current()
        if not actor:
            return {'detail': '登录已失效，请重新登录。'}, 401
        if actor['role'] != 'admin':
            return {'detail': '仅管理员可以查看。'}, 403
        if not identity:
            abort(404)
        try:
            result = central_access(f'admin/users/{user_id}/password/reveal',
                                    {'revision': request.form.get('revision')}, 'POST')
            response = make_response(result)
        except IdentityRejected as exc:
            response = make_response({'detail': exc.message}, exc.status)
        except IdentityUnavailable:
            response = make_response({'detail': '密码查看服务暂不可用，请稍后重试。'}, 503)
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['Pragma'] = 'no-cache'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.post('/admin/requests/<int:user_id>/<app_id>')
    def review_application(user_id, app_id):
        _, response = require_admin()
        if response:
            return response
        if not identity:
            abort(404)
        central_access(f'admin/requests/{user_id}/{app_id}', {'decision': request.form.get('decision'),
                       'note': request.form.get('note', '')}, 'POST')
        return redirect('/admin?' + urlencode({'tab': 'requests', 'message': '申请已处理，结果已同步给用户。'}), code=303)

    @app.post("/admin/user/<int:user_id>")
    def update_user(user_id):
        if identity:
            abort(409, "账号状态与授权请在统一账号中心管理")
        actor, response = require_admin()
        if response:
            return response
        status = request.form.get("status")
        if status not in ("pending", "active", "disabled"):
            abort(400)
        with db() as conn:
            target = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not target:
                abort(404)
            if target["id"] == actor["id"] and status != "active":
                abort(400)
            conn.execute("UPDATE users SET status=? WHERE id=?", (status, user_id))
            if status == "disabled":
                conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            audit(conn, actor["username"], "user_update", f"{target['username']} status={status}")
        return redirect("/admin")

    @app.post("/admin/reset/<int:user_id>")
    def reset_password(user_id):
        if identity:
            abort(409, "密码由统一账号中心管理")
        actor, response = require_admin()
        if response:
            return response
        password = request.form.get("password", "")
        if not 1 <= len(password) <= 128:
            abort(400)
        with db() as conn:
            target = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
            if not target:
                abort(404)
            conn.execute("UPDATE users SET password_hash=?,must_change=1 WHERE id=?",
                         (password_hash(password), user_id))
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            audit(conn, actor["username"], "password_reset", target["username"])
        return redirect("/admin")

    @app.get("/audit")
    def audit_page():
        _, response = require_admin()
        if response:
            return response
        with db() as conn:
            rows = conn.execute("SELECT actor,action,target,created_at FROM audit ORDER BY id DESC LIMIT 100").fetchall()
        body = "<div class='card table-wrap'><table><thead><tr><th>时间（UTC）</th><th>操作者</th><th>操作</th><th>对象</th></tr></thead><tbody>" + "".join(
            f"<tr><td>{datetime.fromtimestamp(r['created_at'],timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}</td><td>{escape(r['actor'])}</td><td>{escape(r['action'])}</td><td>{escape(r['target'])}</td></tr>" for r in rows) + "</tbody></table></div>"
        return page("操作记录", body, subtitle="查看最近 100 条账号操作。")

    @app.errorhandler(IdentityRejected)
    def identity_rejected(error):
        if error.status == 401:
            return redirect('/login', code=303)
        return page('操作未完成', "<section class='panel'><p>请检查填写内容或刷新页面后重试。</p><a href='/permissions'>返回我的权限</a></section>", message=error.message), error.status

    @app.errorhandler(IdentityUnavailable)
    def identity_unavailable(error):
        # Do not render through page(), which would attempt identity lookup again.
        return "账号服务暂时不可用，请稍后刷新重试。", 503

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "private, no-store" if request.path.endswith('/password/reveal') else "no-store"
        return response

    return app


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    bootstrap = sub.add_parser("bootstrap-admin")
    bootstrap.add_argument("username")
    bootstrap.add_argument("--password-file", required=True)
    args = parser.parse_args()
    app = create_app()
    if args.command == "bootstrap-admin":
        name = args.username.strip().lower()
        password = Path(args.password_file).read_text().rstrip("\r\n")
        if not USER_RE.fullmatch(name) or not 1 <= len(password) <= 128:
            parser.error("账号或密码格式不符合要求")
        dbfile = Path(os.environ.get("BIFROST_DATA_DIR") or os.environ.get("ANYDOOR_DATA_DIR") or Path(__file__).parent / ".runtime") / "portal.sqlite3"
        with sqlite3.connect(dbfile) as conn:
            if conn.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
                parser.error("管理员已存在")
            conn.execute("INSERT INTO users(username,password_hash,role,status,created_at) VALUES(?,?,'admin','active',?)",
                         (name, password_hash(password), time.time()))
        print("管理员账号已创建")
    else:
        serve(app, host="127.0.0.1", port=8790, threads=8,
              trusted_proxy="127.0.0.1",
              trusted_proxy_headers={"x-forwarded-host", "x-forwarded-proto"})


if __name__ == "__main__":
    main()
