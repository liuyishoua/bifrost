import os
# from loguru import logger
from dotenv import load_dotenv

dy_auth = None
dy_live_auth = None
def load_env(env_path=None, *, bootstrap_creator=False, proxies=None):
    global dy_auth, dy_live_auth
    load_dotenv(dotenv_path=env_path, override=bool(env_path))
    cookies_dy = os.getenv('DY_COOKIES')
    cookies_live = os.getenv('DY_LIVE_COOKIES')
    from builder.auth import DouyinAuth
    auth_kwargs = dict(
        ticket=os.getenv('DY_TICKET') or None,
        ts_sign=os.getenv('DY_TS_SIGN') or None,
        client_cert=os.getenv('DY_CLIENT_CERT') or None,
        private_key=os.getenv('DY_PRIVATE_KEY') or None,
        dtrait_blob=os.getenv('DY_DTRAIT_BLOB') or None,
        session_dtrait=os.getenv('DY_SESSION_DTRAIT') or None,
        bootstrap_creator=bootstrap_creator,
        proxies=proxies,
    )
    if cookies_dy and cookies_dy.strip():
        # User-provided CK is the authoritative session.  Auth absorbs any
        # subsequent Set-Cookie values into this same object.
        dy_auth = DouyinAuth.open(
            cookies_dy, login_type="cookie", **auth_kwargs,
        )
    else:
        # Empty CK means a real login flow, never a half-initialized anonymous
        # object.  Default ``auto`` chooses QR unless phone is requested via
        # get_auth(..., phone=...).
        dy_auth = DouyinAuth.open(
            login_type="auto", bootstrap_creator=bootstrap_creator,
            proxies=proxies,
        )

    # Current live REST/WebSocket calls reuse the main-site Cookie.  Keep an
    # explicitly supplied DY_LIVE_COOKIES as an opt-in legacy override only.
    dy_live_auth = dy_auth.live_auth(cookies_live)
    return dy_auth


def get_auth(login_type="cookie", *, env_path=None, bootstrap_creator=True,
             timeout=300, show_qr=True, on_qrcode=None, proxies=None,
             phone=None, code=None, auth=None):
    """统一 Auth 入口：cookie / qrcode / phone。"""
    mode = (login_type or "cookie").strip().lower()
    if mode == "cookie":
        return load_env(
            env_path=env_path, bootstrap_creator=bootstrap_creator,
            proxies=proxies,
        )
    if mode in {"qrcode", "qr"}:
        from builder.auth import DouyinAuth
        return DouyinAuth.from_qrcode_login(
            timeout=timeout, show_qr=show_qr, on_qrcode=on_qrcode,
            bootstrap_creator=bootstrap_creator, proxies=proxies,
        )
    if mode in {"phone", "sms"}:
        from builder.auth import DouyinAuth
        if not phone:
            raise ValueError("手机号登录需要 phone")
        if code is None or str(code).strip() == "":
            return DouyinAuth.open(
                login_type="phone", phone=phone, auth=auth,
                bootstrap_creator=bootstrap_creator, proxies=proxies,
            )
        if auth is None:
            raise ValueError(
                "提交手机号验证码必须传入发送验证码时返回的同一个 auth"
            )
        return DouyinAuth.from_phone_login(
            phone, code, auth=auth, bootstrap_creator=bootstrap_creator,
            proxies=proxies,
        )
    raise ValueError(f"不支持的 login_type: {login_type}")


def get_live_auth(auth=None):
    """Return the live session associated with the main Auth.

    ``load_env`` already binds this to the same object unless an explicit
    legacy ``DY_LIVE_COOKIES`` override was supplied.  Keeping this accessor
    makes it hard for callers to accidentally create a second login session.
    """
    global dy_live_auth
    if auth is not None:
        dy_live_auth = auth.live_auth()
    if dy_live_auth is None:
        if dy_auth is None:
            raise RuntimeError("Auth 尚未初始化，请先调用 get_auth()/load_env()")
        dy_live_auth = dy_auth.live_auth()
    return dy_live_auth

def init():
    media_base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../datas/media_datas'))
    excel_base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../datas/excel_datas'))
    for base_path in [media_base_path, excel_base_path]:
        if not os.path.exists(base_path):
            os.makedirs(base_path)
            # logger.info(f'create {base_path}')
    cookies = load_env()
    base_path = {
        'media': media_base_path,
        'excel': excel_base_path,
    }
    return cookies, base_path
