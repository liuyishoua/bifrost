# -*- coding: utf-8 -*-
"""火山引擎 ImageX / VOD 网关 AWS4-HMAC-SHA256 签名（纯算）。

抖音创作者中心的图文走 ImageX、视频走 VOD，两者都用火山 TOP 网关的 V4 签名，
与 AWS SigV4 同构，只有 service 名不同：
  图文 region=cn-north-1 service=imagex  (ApplyImageUpload / CommitImageUpload)
  视频 region=cn-north-1 service=vod     (ApplyUploadInner / CommitUploadInner)
签名头 SessionToken 走 x-amz-security-token，STS 临时密钥来自
`/web/api/media/upload/auth/v5/`（同一份凭证同时授权 ImageX 与 vod 动作）。

实测（2026-08-08）SignedHeaders：
  - GET  无 body： x-amz-date;x-amz-security-token
  - POST 有 body： x-amz-content-sha256;x-amz-date;x-amz-security-token
"""

import hashlib
import hmac
import time
import urllib.parse


IMAGEX_HOST = "imagex.bytedanceapi.com"
IMAGEX_REGION = "cn-north-1"
IMAGEX_SERVICE = "imagex"
VOD_HOST = "vod.bytedanceapi.com"
VOD_SERVICE = "vod"
_ALGORITHM = "AWS4-HMAC-SHA256"


def _sha256_hex(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _hmac(key, msg):
    if isinstance(msg, str):
        msg = msg.encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).digest()


def _canonical_query(query_params):
    """按 key 排序并 RFC3986 编码。query_params: dict 或有序键值列表。"""
    if isinstance(query_params, dict):
        items = list(query_params.items())
    else:
        items = list(query_params)
    encoded = [
        (urllib.parse.quote(str(k), safe="-_.~"),
         urllib.parse.quote(str(v), safe="-_.~"))
        for k, v in items
    ]
    encoded.sort()
    return "&".join(f"{k}={v}" for k, v in encoded)


def sign_request(access_key, secret_key, session_token, *, method,
                 query_params, body=b"", now=None,
                 service=IMAGEX_SERVICE, region=IMAGEX_REGION):
    """生成火山网关 V4 签名头。

    :param access_key: STS AccessKeyID
    :param secret_key: STS SecretAccessKey
    :param session_token: STS SessionToken（进 x-amz-security-token）
    :param method: 'GET' / 'POST'
    :param query_params: 请求 query（dict/有序列表），需与实际 URL 一致
    :param body: POST body 字节（GET 传 b''）
    :param service: 网关服务名，图文 imagex / 视频 vod
    :param region: 网关地域，均为 cn-north-1
    :return: dict 待合入请求的头
    """
    if isinstance(body, str):
        body = body.encode("utf-8")
    method = method.upper()
    t = time.gmtime(now if now is not None else time.time())
    amz_date = time.strftime("%Y%m%dT%H%M%SZ", t)
    date_stamp = time.strftime("%Y%m%d", t)

    payload_hash = _sha256_hex(body)

    # 参与签名的头：POST 多一个 x-amz-content-sha256
    signed_map = {
        "x-amz-date": amz_date,
        "x-amz-security-token": session_token,
    }
    if method == "POST":
        signed_map["x-amz-content-sha256"] = payload_hash
    signed_headers = ";".join(sorted(signed_map.keys()))
    canonical_headers = "".join(
        f"{k}:{signed_map[k]}\n" for k in sorted(signed_map.keys())
    )

    canonical_request = "\n".join([
        method,
        "/",
        _canonical_query(query_params),
        canonical_headers,
        signed_headers,
        payload_hash,
    ])

    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        _ALGORITHM,
        amz_date,
        credential_scope,
        _sha256_hex(canonical_request),
    ])

    k_date = _hmac(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    k_signing = _hmac(k_service, "aws4_request")
    signature = hmac.new(
        k_signing, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    authorization = (
        f"{_ALGORITHM} Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    headers = {
        "authorization": authorization,
        "x-amz-date": amz_date,
        "x-amz-security-token": session_token,
    }
    if method == "POST":
        headers["x-amz-content-sha256"] = payload_hash
    return headers
