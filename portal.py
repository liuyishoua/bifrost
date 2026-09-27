"""Bifrost account portal. Business HTTP traffic never passes through this process."""
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
from urllib.parse import quote, urlsplit
import hashlib

from flask import Flask, abort, g, make_response, redirect, render_template_string, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash
from waitress import serve

from app_registry import AppRegistry
from gateway_routes import GatewayError, GatewayRoutes
from service_control import Controller, ServiceError

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
"""
PAGE = """<!doctype html><html lang=zh-CN><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>{{ title }} · Bifrost</title><style>
:root{font-family:system-ui,sans-serif;color:#273149;background:#f5f7fb}body{margin:0}header{background:#1c2750;color:white;padding:16px max(24px,calc((100vw - 980px)/2));display:flex;justify-content:space-between;align-items:center}header a{color:white;margin-left:18px}main{max-width:980px;margin:42px auto;padding:0 24px}h1{font-size:30px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px}.card,form.panel{background:white;border:1px solid #e1e5ed;border-radius:14px;padding:24px;box-shadow:0 4px 18px #1c27500a}.card p{color:#65718a}label{display:block;margin:14px 0}input[type=text],input[type=password]{display:block;padding:11px;margin-top:5px;box-sizing:border-box;width:100%;border:1px solid #bac3d3;border-radius:8px}button,.button{display:inline-block;background:#315ed1;color:white;border:0;border-radius:8px;padding:10px 16px;text-decoration:none;cursor:pointer}button.secondary{background:#5f6982}form.inline{display:inline}.notice{padding:12px 16px;background:#fff5d9;border-radius:8px;margin:14px 0}.muted{color:#65718a}table{width:100%;border-collapse:collapse;background:white}td,th{padding:12px;text-align:left;border-bottom:1px solid #e1e5ed}fieldset{border:0;padding:0}small{color:#65718a}
</style><header><b>Bifrost</b><nav>{% if user %}<a href='/'>应用</a><a href='/settings'>个人设置</a>{% if user['role']=='admin' %}<a href='/admin'>管理</a><a href='/audit'>记录</a>{% endif %}<form class=inline method=post action='/logout'><input type=hidden name=csrf value='{{ csrf_token }}'><button class=secondary>退出</button></form>{% endif %}</nav></header><main><h1>{{ title }}</h1>{% if message %}<p class=notice>{{ message }}</p>{% endif %}{{ body|safe }}</main></html>"""


def create_app(runtime=None, registry=None):
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
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 65536
    public_origin = (os.environ.get("BIFROST_PUBLIC_ORIGIN") or os.environ.get("ANYDOOR_PUBLIC_ORIGIN") or "http://127.0.0.1:8080").rstrip("/")
    verify_origin = (os.environ.get("BIFROST_VERIFY_ORIGIN") or os.environ.get("ANYDOOR_VERIFY_ORIGIN") or "http://127.0.0.1:8081").rstrip("/")
    if urlsplit(public_origin).scheme not in ("http", "https"):
        raise ValueError("BIFROST_PUBLIC_ORIGIN must be an HTTP origin")
    secure_cookie = urlsplit(public_origin).scheme == "https"
    if registry is None:
        registry = AppRegistry(Path(__file__).parent / "applications", runtime, public_origin)
        registry.refresh()
    gateway = GatewayRoutes(runtime, public_origin, os.environ.get("BIFROST_CADDY_CONFIG"))
    gateway.sync(registry.all(), reload=False)
    controller = Controller(runtime, registry)
    app.extensions["controller"] = controller
    app.extensions["app_registry"] = registry
    app.extensions["gateway_routes"] = gateway
    last_discovery_errors = {}

    def apps():
        return {item.id: item for item in registry.all()}

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
        token = request.cookies.get("portal_session", "")
        with db() as conn:
            g.user = conn.execute("""SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
                WHERE s.token_hash=? AND s.expires_at>? AND u.status='active'""",
                (digest(token), time.time())).fetchone() if token else None
        return g.user

    def csrf():
        if not getattr(g, "csrf_token", None):
            binding = digest(request.cookies.get("portal_session", "")) if request.cookies.get("portal_session") else "anonymous"
            g.csrf_token = csrf_signer.dumps({"binding": binding, "nonce": secrets.token_urlsafe(16)})
        return g.csrf_token

    def form_csrf():
        return f'<input type=hidden name=csrf value="{escape(csrf())}">'

    def page(title, body, message=""):
        response = make_response(render_template_string(PAGE, title=title, body=body, message=message, user=current(), csrf_token=csrf()))
        response.headers["Cache-Control"] = "no-store"
        return response

    def allowed_next(value):
        destinations = {"/"}
        destinations.update(a.path if a.kind == "legacy" else a.origin + "/" for a in registry.all())
        return value if value in destinations else "/"

    def require_user():
        user = current()
        if not user:
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
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and not request.path.startswith("/internal/"):
            submitted = request.form.get("csrf", "")
            try:
                token = csrf_signer.loads(submitted, max_age=12 * 3600)
            except (BadSignature, SignatureExpired):
                token = {}
            binding = digest(request.cookies.get("portal_session", "")) if request.cookies.get("portal_session") else "anonymous"
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
        if request.method == "POST":
            name = request.form.get("username", "").strip().lower()
            password = request.form.get("password", "")
            with db() as conn:
                blocked = limited(conn, "register", request.remote_addr or "", 5, 3600)
                if blocked:
                    return page("注册", register_form(), "注册过于频繁，请稍后再试"), 429
                if not USER_RE.fullmatch(name) or not 10 <= len(password) <= 128:
                    return page("注册", register_form(), "账号或密码格式不符合要求"), 400
                try:
                    conn.execute("INSERT INTO users(username,password_hash,role,status,created_at) VALUES(?,?,'user','pending',?)",
                                 (name, password_hash(password), time.time()))
                    audit(conn, name, "register", name)
                except sqlite3.IntegrityError:
                    return page("注册", register_form(), "账号名已存在"), 409
            return page("待审批", "<p>注册成功，请等待管理员审批并分配应用权限。</p><a href='/login'>去登录</a>")
        return page("注册", register_form(), "页面已更新，请重新填写" if request.args.get("expired") else "")

    def register_form():
        return f"<form class=panel method=post>{form_csrf()}<label>账号<input name=username type=text required minlength=3 maxlength=32></label><label>密码<input name=password type=password required minlength=10 maxlength=128></label><button>注册</button> <a href='/login'>已有账号</a></form>"

    @app.route("/login", methods=["GET", "POST"])
    def login():
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
        return f"<form class=panel method=post>{form_csrf()}<input type=hidden name=next value='{escape(target)}'><label>账号<input name=username type=text required></label><label>密码<input name=password type=password required></label><label><input name=remember type=checkbox value=1> 保持登录 30 天</label><button>登录</button> <a href='/register'>注册</a></form>"

    @app.post("/logout")
    def logout():
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
        with db() as conn:
            grants = {r[0] for r in conn.execute("SELECT app_id FROM grants WHERE user_id=?", (user["id"],))}
        cards = []
        for item in registry.all():
            if user["role"] != "admin" and item.id not in grants:
                continue
            details = ""
            if user["role"] == "admin":
                state, reason = controller.status(item.id)
                action = "stop" if state == "running" else "start"
                button = f"<form class=inline method=post action='/admin/service/{item.id}/{action}'>{form_csrf()}<button class=secondary>{'关闭' if action=='stop' else '启动'}</button></form>" if state in ("running", "stopped") else ""
                details = f"<p>状态：{escape(state)} {escape(reason)}</p>{button}"
            destination = item.path if item.kind == "legacy" else item.origin + "/"
            cards.append(f"<article class=card><h2>{escape(item.name)}</h2><p>{escape(item.description)}</p><a class=button href='{escape(destination)}'>进入应用</a>{details}</article>")
        return page("我的应用", "<p class=muted>同一应用的获授权用户共用业务实例与数据。</p><div class=grid>" + "".join(cards) + "</div>")

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        user, response = require_user()
        if response:
            return response
        message = "使用临时密码登录后，请先修改密码。" if user["must_change"] else ""
        if request.method == "POST":
            old, new = request.form.get("old", ""), request.form.get("new", "")
            if not check_password_hash(user["password_hash"], old) or not 10 <= len(new) <= 128:
                message = "旧密码错误或新密码长度不符合要求"
            else:
                with db() as conn:
                    conn.execute("UPDATE users SET password_hash=?,must_change=0 WHERE id=?",
                                 (password_hash(new), user["id"]))
                    conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
                    audit(conn, user["username"], "password_change", user["username"])
                response = issue(redirect("/"), user["id"])
                return response
        return page("个人设置", f"<form class=panel method=post>{form_csrf()}<label>当前密码<input name=old type=password required></label><label>新密码<input name=new type=password minlength=10 maxlength=128 required></label><button>修改密码</button></form>", message)

    @app.get("/admin")
    def admin():
        nonlocal last_discovery_errors
        actor, response = require_admin()
        if response:
            return response
        registry.refresh()
        for app_id in tuple(registry.pinned):
            controller.status(app_id)
        gateway_error = ""
        try:
            gateway.sync(registry.all())
        except GatewayError as exc:
            gateway_error = str(exc)
        newly_invalid = {key: error for key, error in registry.errors.items()
                         if last_discovery_errors.get(key) != error}
        last_discovery_errors = dict(registry.errors)
        with db() as conn:
            for key, error in newly_invalid.items():
                audit(conn, actor["username"], "app_validation_error", f"{key}:{error}")
            if gateway_error:
                audit(conn, actor["username"], "gateway_reload_error", gateway_error)
            users = conn.execute("SELECT id,username,role,status,must_change FROM users ORDER BY id DESC").fetchall()
            grants = {(r[0], r[1]) for r in conn.execute("SELECT user_id,app_id FROM grants")}
        rows = []
        for user in users:
            controls = f"<form method=post action='/admin/user/{user['id']}'>{form_csrf()}<select name=status><option value=pending>待审批</option><option value=active {'selected' if user['status']=='active' else ''}>启用</option><option value=disabled {'selected' if user['status']=='disabled' else ''}>禁用</option></select> "
            controls += " ".join(f"<label><input type=checkbox name=grant value='{a.id}' {'checked' if (user['id'],a.id) in grants else ''}> {escape(a.name)}</label>" for a in registry.all())
            controls += "<button>保存</button></form>"
            controls += f"<form method=post action='/admin/reset/{user['id']}'>{form_csrf()}<label>临时密码<input type=password name=password minlength=10 maxlength=128 required></label><button class=secondary>重置密码</button></form>"
            rows.append(f"<tr><td>{escape(user['username'])}</td><td>{escape(user['role'])}</td><td>{controls}</td></tr>")
        discovered = "".join(f"<li>{escape(item.name)} · {escape(item.origin or item.path)}</li>"
                             for item in registry.all())
        problems = "".join(f"<li>{escape(key)}：{escape(error)}</li>"
                           for key, error in registry.errors.items())
        if gateway_error:
            problems += f"<li>网关：{escape(gateway_error)}</li>"
        body = ("<section class=card><h2>应用接入</h2><ul>" + discovered + problems + "</ul></section>"
                "<table><tr><th>账号</th><th>角色</th><th>操作</th></tr>" + "".join(rows) + "</table>")
        return page("账号管理", body)

    @app.post("/admin/user/<int:user_id>")
    def update_user(user_id):
        actor, response = require_admin()
        if response:
            return response
        status = request.form.get("status")
        chosen = set(request.form.getlist("grant"))
        if status not in ("pending", "active", "disabled") or not chosen <= apps().keys():
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
            conn.execute("DELETE FROM grants WHERE user_id=?", (user_id,))
            conn.executemany("INSERT INTO grants VALUES(?,?)", ((user_id, app_id) for app_id in chosen))
            audit(conn, actor["username"], "user_update", f"{target['username']} status={status} grants={','.join(sorted(chosen))}")
        return redirect("/admin")

    @app.post("/admin/reset/<int:user_id>")
    def reset_password(user_id):
        actor, response = require_admin()
        if response:
            return response
        password = request.form.get("password", "")
        if not 10 <= len(password) <= 128:
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
        body = "<table><tr><th>时间</th><th>操作者</th><th>操作</th><th>对象</th></tr>" + "".join(
            f"<tr><td>{datetime.fromtimestamp(r['created_at'],timezone.utc).isoformat()}</td><td>{escape(r['actor'])}</td><td>{escape(r['action'])}</td><td>{escape(r['target'])}</td></tr>" for r in rows) + "</table>"
        return page("操作记录", body)

    @app.post("/admin/service/<app_id>/<action>")
    def service_operation(app_id, action):
        actor, response = require_admin()
        if response:
            return response
        if app_id not in apps() or action not in ("start", "stop"):
            abort(404)
        with db() as conn:
            audit(conn, actor["username"], "service_request", f"{app_id}:{action}")
        try:
            result = controller.operate(app_id, action)
            code = 200
        except ServiceError as exc:
            result, code = str(exc), 409
        with db() as conn:
            audit(conn, actor["username"], "service_result", f"{app_id}:{action}:{result}")
        if code != 200:
            return page("服务操作", "<a href='/'>返回应用</a>", result), code
        return redirect("/")

    @app.get("/internal/auth/<app_id>")
    def internal_auth(app_id):
        if request.remote_addr not in ("127.0.0.1", "::1") or app_id not in apps():
            abort(404)
        method = request.headers.get("X-Forwarded-Method", "GET").upper()
        original = request.headers.get("X-Forwarded-Uri", "")
        item = apps()[app_id]
        expected = item.path if item.kind == "legacy" else item.origin + "/"
        verify = request.headers.get("X-Bifrost-Verification") == "1"
        if verify:
            if app_id != "douyin" or not (original.split("?", 1)[0].startswith("/verification-request/") or
                    original.split("?", 1)[0] in ("/verification-frame", "/static/verification-frame.js")):
                app.logger.warning("auth_path_rejected app=%s path=%s verify=%s", app_id, original.split("?", 1)[0], verify)
                abort(403)
        elif item.kind == "manifest":
            forwarded = urlsplit(item.origin)
            path = original.split("?", 1)[0]
            if (request.headers.get("X-Forwarded-Host") != forwarded.netloc or
                    request.headers.get("X-Forwarded-Proto") != forwarded.scheme or
                    not path.startswith("/") or path.startswith("//") or ".." in path.split("/")):
                abort(403)
        elif not (original.split("?", 1)[0] == expected[:-1] or original.split("?", 1)[0].startswith(expected)):
            app.logger.warning("auth_path_rejected app=%s path=%s verify=%s", app_id,
                               original.split("?", 1)[0], verify)
            abort(403)
        user = current()
        if not user:
            accept = request.headers.get("Accept", "")
            if method in ("GET", "HEAD") and "text/html" in accept and not verify:
                destination = (public_origin + "/login?next=" + quote(expected, safe="")
                               if item.kind == "manifest" else "/login?next=" + expected)
                return redirect(destination)
            return "未登录", 401
        if user["must_change"]:
            return "请先修改密码", 403
        if user["role"] != "admin":
            with db() as conn:
                permitted = conn.execute("SELECT 1 FROM grants WHERE user_id=? AND app_id=?",
                                         (user["id"], app_id)).fetchone()
            if not permitted:
                return "未获授权", 403
        if app_id in controller.transitioning:
            return "服务正在启停", 503
        if method not in ("GET", "HEAD", "OPTIONS"):
            source = request.headers.get("Origin", "")
            required = verify_origin if verify else item.origin if item.kind == "manifest" else public_origin
            if source != required:
                return "请求来源无效", 403
        return "", 204

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
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
        if not USER_RE.fullmatch(name) or not 10 <= len(password) <= 128:
            parser.error("账号或密码格式不符合要求")
        dbfile = Path(os.environ.get("BIFROST_DATA_DIR") or os.environ.get("ANYDOOR_DATA_DIR") or Path(__file__).parent / ".runtime") / "portal.sqlite3"
        with sqlite3.connect(dbfile) as conn:
            if conn.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
                parser.error("管理员已存在")
            conn.execute("INSERT INTO users(username,password_hash,role,status,created_at) VALUES(?,?,'admin','active',?)",
                         (name, password_hash(password), time.time()))
        print("管理员账号已创建")
    else:
        serve(app, host="127.0.0.1", port=8790, threads=8)


if __name__ == "__main__":
    main()
