# -*- coding: utf-8 -*-
"""passport（登录）相关的纯算工具。

包含两块：

1. **手机号 / 验证码 / 密码的加密**：ByteDance passport 对这些字段做逐字符
   `XOR 5` 后转两位小写十六进制。
2. **bd-ticket-guard 引导**：`ticket` / `ts_sign` / `client_cert` 由服务端在
   **登录响应**里下发（base64 的 JSON），响应头 `bd-ticket-guard-server-data`
   与同名 Cookie 两个渠道都可能给。客户端把自己的公钥交上去即可，
   不需要读浏览器 localStorage。
   （机制逆自 passport `zero.js`：`b642str(...) -> JSON -> {ticket, ts_sign,
   client_cert, create_time, log_id}`。）

   **公钥是走 Cookie 交上去的，不是请求头**——这点长期理解错了：登出态实录里
   passport 请求的 `Access-Control-Request-Headers` 只有 5 个自定义头，
   没有任何 `bd-ticket-guard-*`，于是误以为登录阶段根本没把公钥交给服务端。
   实际 zero.js 是写 Cookie：

   ```js
   u = {"bd-ticket-guard-version":2, "bd-ticket-guard-iteration-version":1,
        "bd-ticket-guard-ree-public-key": c, "bd-ticket-guard-web-version":2}
   cookieOperate.setCookieWithDomain("bd_ticket_guard_client_data", b64(JSON.stringify(u)))
   cookieOperate.setCookieWithDomain("bd_ticket_guard_client_web_domain", "2")
   ```

   `build_client_data_cookie()` 复现这个值，`bootstrap_auth` 在取二维码前就得带上，
   否则服务端不知道该把 ticket 签给哪把公钥，登录成功也拿不到 ticket。
"""

import base64
import json
import urllib.parse

from ecdsa import SigningKey, NIST256p

_XOR_KEY = 5

CLIENT_DATA_COOKIE = "bd_ticket_guard_client_data"
CLIENT_WEB_DOMAIN_COOKIE = "bd_ticket_guard_client_web_domain"
SERVER_DATA_COOKIE = "bd_ticket_guard_server_data"


def passport_encrypt(text) -> str:
    """passport 字段加密：UTF-8 字节逐个 XOR 5，直接拼接小写 hex。

    Chrome 的 passport SDK 对表单字段先取 UTF-8 bytes，再对每个 byte
    做 XOR 5；输出使用 ``toString(16)`` 风格的非定宽小写十六进制，
    因而不能按 Python 字符的 ``ord`` 处理，也不能强制补零。
    """
    return "".join(format(byte ^ _XOR_KEY, "x")
                   for byte in str(text).encode("utf-8"))


def generate_ec_keypair():
    """生成 bd-ticket-guard 用的 P-256 密钥对（PEM）。"""
    sk = SigningKey.generate(curve=NIST256p)
    return sk.to_pem().decode(), sk.get_verifying_key().to_pem().decode()


def build_client_data_cookie(private_key: str) -> str:
    """`bd_ticket_guard_client_data` 的值：把自己的公钥交给服务端。

    键序照 zero.js 原样，值是 base64(JSON) 再经 `encodeURIComponent`
    （实录 Cookie 里 `==` 是 `%3D%3D`）。
    """
    from utils.bd_ticket import get_ree_key
    payload = {
        "bd-ticket-guard-version": 2,
        "bd-ticket-guard-iteration-version": 1,
        "bd-ticket-guard-ree-public-key": get_ree_key(private_key),
        "bd-ticket-guard-web-version": 2,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return urllib.parse.quote(base64.b64encode(raw.encode()).decode(), safe="")


def build_client_data_v2_cookie(private_key: str, sec_ts: str, server_cert: str,
                                ts_sign: str = "") -> str:
    """`bd_ticket_guard_client_data_v2` 的值。

    结构（2026-08-22 从浏览器 cookie 解出）::

        {"ree_public_key": <我们的公钥>,
         "ts_sign":        <服务端签发的 ts_sign，没有就省略>,
         "req_content":    "sec_ts",
         "req_sign":       base64(HMAC-SHA256(ecdhKey, "sec_ts=" + sec_ts)),
         "sec_ts":         <get_sec_ts 接口返回的那串，以 # 开头>}

    `req_sign` 的被签内容是**离线爆破出来的**：拿浏览器 localStorage 里的
    EC 私钥 + 服务端 ecies 证书推出 ecdhKey，再对候选拼法逐个 HMAC 去撞
    cookie 里的真值，`"sec_ts=" + sec_ts` 一发命中；该候选拼法已通过历史
    浏览器抓包做过离线校验。好在它**不含时间戳**，所以可以离线复现、也可以
    反复校验。

    与 `bd-ticket-guard-*` 请求头用的是同一把 ecdhKey（ECDH-P256 + HKDF-SHA256），
    区别只在被签内容：请求头签的是 `ticket=..&path=..&timestamp=..`。
    """
    import base64 as _b64
    import hashlib
    import hmac as _hmac
    from utils.bd_ticket import get_ree_key, derive_ecdh_key

    ecdh_key = derive_ecdh_key(private_key, server_cert)
    req_sign = _b64.b64encode(
        _hmac.new(ecdh_key, ("sec_ts=" + sec_ts).encode(), hashlib.sha256).digest()
    ).decode()
    payload = {"ree_public_key": get_ree_key(private_key)}
    if ts_sign:
        payload["ts_sign"] = ts_sign
    payload["req_content"] = "sec_ts"
    payload["req_sign"] = req_sign
    payload["sec_ts"] = sec_ts
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return urllib.parse.quote(base64.b64encode(raw.encode()).decode(), safe="")


def merge_set_cookies(auth, set_cookies):
    """把 Set-Cookie 合进 auth.cookie，但**丢掉被删除的**。

    服务端用 `name=; Max-Age=0` 表示「删掉这个 Cookie」（实录里
    `reg-store-region=` 就是这样）。`resp.cookies.get_dict()` 只给名值对，
    删除指令看起来就是个空值，直接 update 进去等于凭空多发一个空 Cookie ——
    而浏览器是不会发它的。「值为空」和「字段不存在」是两回事。
    """
    for k, v in (set_cookies or {}).items():
        if v == "":
            auth.cookie.pop(k, None)
            marker = getattr(auth, "mark_cookie_source", None)
            if marker:
                marker(k, "server_delete")
        else:
            auth.cookie[k] = v
            marker = getattr(auth, "mark_cookie_source", None)
            if marker:
                marker(k, "server_set_cookie")
    raw = set(getattr(auth, "_raw_cookie_tokens", ()) or ())
    sync = getattr(auth, "_sync_cookie_str", None)
    if sync:
        sync()
    else:
        parts = [
            k if k in raw else f"{k}={x}"
            for k, x in auth.cookie.items()
        ]
        parts.extend(token for token in raw if token not in auth.cookie)
        auth.cookie_str = "; ".join(parts)
    return auth.cookie


def parse_ticket_guard_server_data(headers, cookies=None):
    """解出服务端下发的 ticket 信息。

    :param headers: requests 的响应头（大小写不敏感）。
    :param cookies: 该响应的 Set-Cookie（dict）；服务端也可能把同一份 base64
        JSON 放在 `bd_ticket_guard_server_data` Cookie 里下发。
    :return: dict 或 None，含 ticket / ts_sign / client_cert / create_time / log_id
    """
    raw = (headers or {}).get("bd-ticket-guard-server-data") or None
    if not raw and cookies:
        raw = cookies.get(SERVER_DATA_COOKIE)
    if not raw:
        return None
    raw = urllib.parse.unquote(raw)
    try:
        info = json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)))
    except Exception:
        return None
    if not info.get("ticket"):
        return None
    # zero.js 里 ts_sign 只取前 64 字符，client_cert 去掉 "pub." 前缀
    return {
        "ticket": info.get("ticket", ""),
        "ts_sign": info.get("ts_sign", ""),
        "client_cert": info.get("client_cert", ""),
        "create_time": info.get("create_time"),
        "log_id": info.get("log_id", ""),
    }


def apply_ticket_guard(auth, headers, cookies=None) -> bool:
    """若响应里带了新签发的 ticket，就写回 auth。返回是否更新成功。"""
    info = parse_ticket_guard_server_data(headers, cookies)
    if not info:
        return False
    auth.ticket = info["ticket"]
    auth.ts_sign = info["ts_sign"]
    auth.client_cert = info["client_cert"]
    return True
