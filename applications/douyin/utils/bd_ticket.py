# -*- coding: utf-8 -*-
"""bd-ticket-guard

签名分两种（对齐 passport 的 zero.js `n2` / `deriveEcdhKey`）：
- ``hmac``：客户端 EC 私钥与服务端 ecies 证书公钥做 ECDH，经 HKDF-SHA256 得到 32 字节
  会话密钥，再对 sign_data 做 HMAC-SHA256。新版证书（``client_cert`` 形如 ``pub.<b64>``）
  走这条，请求头 ``bd-ticket-guard-web-sign-type: 1``。
- ``ecdsa``：直接用 EC 私钥对 sign_data 做 ECDSA-SHA256（DER），
  ``bd-ticket-guard-web-sign-type: 0``，仅在拿不到服务端证书时兜底。
"""

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from utils import http_client as requests
from ecdsa import SigningKey, VerifyingKey, NIST256p
from ecdsa.util import sigencode_der

# P-256 的 SubjectPublicKeyInfo 前缀，其后紧跟 64 字节裸公钥点（X||Y）。
# 服务端 ecies 证书固定是 P-256，按此定位可免引入完整 X.509 解析依赖。
_P256_SPKI_PREFIX = bytes.fromhex(
    "3059301306072a8648ce3d020106082a8648ce3d03010703420004"
)

GET_CLIENT_CERT_API = "/passport/ticket_guard/get_client_cert/"


def _load_signing_key(prv) -> SigningKey:
    if "-----BEGIN" in prv:
        return SigningKey.from_pem(prv)
    return SigningKey.from_string(bytes.fromhex(prv), curve=NIST256p)


def _pem_body(pem: str) -> bytes:
    body = "".join(
        line for line in pem.strip().splitlines() if "-----" not in line
    )
    return base64.b64decode(body)


def _server_pub_point(server_cert: str) -> bytes:
    """从服务端证书里取出 65 字节未压缩公钥点（含 0x04 前缀）。

    兼容两种下发格式：完整 PEM 证书，或新版的 ``pub.<base64 裸公钥点>``。
    """
    if server_cert.startswith("pub."):
        return base64.b64decode(server_cert[4:])
    der = _pem_body(server_cert)
    idx = der.find(_P256_SPKI_PREFIX)
    if idx < 0:
        raise ValueError("服务端证书中未找到 P-256 公钥")
    start = idx + len(_P256_SPKI_PREFIX)
    return b"\x04" + der[start:start + 64]


def _hkdf_sha256(ikm: bytes, length: int = 32, salt: bytes = b"", info: bytes = b"") -> bytes:
    if not salt:
        salt = b"\x00" * hashlib.sha256().digest_size
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm, block, counter = b"", b"", 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def derive_ecdh_key(prv: str, server_cert: str) -> bytes:
    """ECDH(客户端私钥, 服务端证书公钥) -> HKDF-SHA256 -> 32 字节 HMAC 密钥。"""
    sk = _load_signing_key(prv)
    vk = VerifyingKey.from_string(_server_pub_point(server_cert), curve=NIST256p)
    shared_point = sk.privkey.secret_multiplier * vk.pubkey.point
    shared = shared_point.x().to_bytes(32, "big")
    return _hkdf_sha256(shared)


def fetch_server_cert(aid, cookie_str, origin="https://www.douyin.com",
                      user_agent=None, proxies=None, session_dtrait=None,
                      csrf_token=None, ms_token=None, referer=None):
    """取服务端 ecies 证书（浏览器的 get_client_cert）。

    对齐 SDK：body 的键值对用 ``,`` 而非 ``&`` 连接（见 zero.js ``tu``）。

    :param session_dtrait: `x-tt-session-dtrait` 头，实录里这个请求是带的。
    :param csrf_token: `x-secsdk-csrf-token`，由 secsdk 的取 token 流程下发；
        没有就不发（实录里有，但缺它是否被拒尚未实测）。
    :param ms_token: optional session mssdk token. Chrome reuses the token
        obtained immediately before this request; generating a short random
        fallback changes the query shape.
    :param referer: page URL used as the HTTP Referer; callers can freeze it
        for a browser fixture replay.
    :return: (server_cert, server_sn)
    """
    # 延迟导入：utils.dy_util 会反向用到本模块，模块级导入会成环
    from utils.dy_util import generate_a_bogus, generate_msToken
    from utils.fingerprint import get_profile

    profile = get_profile()
    query = {
        "aid": str(aid),
        "is_from_ttaccountsdk": "1",
        "msToken": ms_token or generate_msToken(),
    }
    query["a_bogus"] = generate_a_bogus(urlencode(query))
    url = f"{origin}{GET_CLIENT_CERT_API}?{urlencode(query)}"
    # JS-set header order follows the ticket-guard capture. Chrome's network
    # layer additionally emits origin/fetch metadata and Cookie; curl_cffi's
    # default headers are disabled, so include those explicitly.
    # Chrome 151 reqid=494's JS-controlled header order is exactly:
    # x-tt-session-dtrait, referer, user-agent, accept,
    # x-secsdk-csrf-token, content-type.  The client-hint/cache headers that
    # older captures contained are not sent by this current request.
    headers = {
        "x-tt-session-dtrait": session_dtrait or "",
        "referer": referer or f"{origin}/",
        "user-agent": user_agent or profile["ua"],
        "accept": "application/json",
    }
    if csrf_token is None:
        # 实录里这个请求是带 x-secsdk-csrf-token 的，没传就自己去换一个
        try:
            from utils.dy_util import generate_csrf_token
            csrf_token, _ = generate_csrf_token(cookie_str or "")
        except Exception:
            csrf_token = ""
    if csrf_token:
        headers["x-secsdk-csrf-token"] = csrf_token
    # Keep the JS-set order from Chrome's ticket-guard capture:
    # Accept -> x-secsdk-csrf-token -> Content-Type.  The remaining fetch
    # metadata is added after this block; curl_cffi keeps the mapping order
    # and the cookie is inserted immediately after content-type below.
    headers.update({
        "content-type": "application/x-www-form-urlencoded",
        # These are network-layer additions observed after the controlled
        # headers; keep them explicit because curl_cffi default headers are
        # disabled, but do not add cache-control/pragma/sec-ch-ua*.
        "accept-language": "zh-CN,zh;q=0.9",
        "origin": origin,
        "priority": "u=1, i",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    })
    # Put Cookie after content-type, matching Chrome's copied-cURL/wire
    # position. Never pass it through curl_cffi's cookie jar: that jar sorts
    # by its own insertion rules and loses browser order.
    with_cookie = {}
    for name, value in headers.items():
        with_cookie[name] = value
        if name == "content-type":
            with_cookie["cookie"] = cookie_str or ""
    resp = requests.post(url, headers=with_cookie,
                         data=f"server_data=1,aid={aid}",
                         verify=False, proxies=proxies, timeout=15)
    res_json = resp.json()
    if res_json.get("message") != "success":
        raise RuntimeError(f"获取服务端证书失败: {res_json}")
    data = res_json.get("data") or {}
    cert, sn = data.get("server_cert", ""), data.get("server_sn", "")
    if not cert:
        raise RuntimeError(f"服务端证书为空: {res_json}")
    return cert, sn


def get_ree_key(prv) -> str:
    sk = _load_signing_key(prv)
    vk = sk.get_verifying_key()
    return base64.b64encode(b"\x04" + vk.to_string()).decode()


def get_req_sign(e, prv) -> str:
    if isinstance(e, (dict, list)):
        e = json.dumps(e, ensure_ascii=False, separators=(",", ":"))
    sk = _load_signing_key(prv)
    signature = sk.sign(e.encode("utf-8"), hashfunc=hashlib.sha256, sigencode=sigencode_der)
    return base64.b64encode(signature).decode()


def get_req_sign_hmac(e, ecdh_key: bytes) -> str:
    if isinstance(e, (dict, list)):
        e = json.dumps(e, ensure_ascii=False, separators=(",", ":"))
    return base64.b64encode(
        hmac.new(ecdh_key, e.encode("utf-8"), hashlib.sha256).digest()
    ).decode()


def generate_bd_ticket_client_data(api: str, ticket: str, ts_sign: str, prv: str,
                                   ecdh_key: bytes = None, timestamp: int = None,
                                   t_trust: int = None):
    """生成 bd-ticket-guard-client-data。

    :param api: 请求 pathname（不含 query）。
    :param ecdh_key: 有则走 HMAC，无则回退 ECDSA。
    :return: (client_data_b64, algo_type)，algo_type 为 'hmac' / 'ecdsa'。
    """
    timestamp = int(time.time()) if timestamp is None else int(timestamp)
    res_sign = f"ticket={ticket}&path={api}&timestamp={timestamp}"
    if ecdh_key:
        req_sign, algo_type = get_req_sign_hmac(res_sign, ecdh_key), "hmac"
    else:
        req_sign, algo_type = get_req_sign(res_sign, prv), "ecdsa"
    p = {
        "ts_sign": ts_sign,
        "req_content": "ticket,path,timestamp",
        "req_sign": req_sign,
        "timestamp": timestamp,
    }
    # 新版 ticket-guard 在 `_bd_ticket_crypt_cookie` 已建立后追加这一项。
    # 它位于 timestamp 之后；无 trust cookie 的早期阶段仍保持旧四字段结构。
    if t_trust is not None:
        p["t_trust"] = int(t_trust)
    p = json.dumps(p, ensure_ascii=False, separators=(",", ":"))
    # 浏览器用 btoa()，是标准 base64（+/），不是 urlsafe（-_）
    return base64.b64encode(p.encode("utf-8")).decode(), algo_type


def ticket_guard_version(ts_sign: str) -> int:
    """web-version 由 ts_sign 前缀决定（zero.js `nQ`）：ts.1 -> 1，其余 -> 2。"""
    return 1 if (ts_sign or "").startswith("ts.1") else 2
