# -*- coding: utf-8 -*-
"""strData 上报体纯算法生成（mssdk /web/r/token）。

⚠️ 2026-08-22 重写：**协议换代了**。

老实现发的是 `{"tokenList":[],"navigator":{...},"wID":{...18 个字段},"window":{...},
"webgl":{},"document":{...},"screen":{...},"plugins":{...},"custom":{}}`，
2355 字节。而浏览器实录（无痕态跑真实登录，解开 strData 得到明文）是：

    {"nWID": {navigator, canvas, screen, extra, webgl, audio, video, math,
              envCode, ubCode, ms_accid, custom, ms_version},
     "wID":  {"msgType": 3}}

7143 字节 —— **顶层只有 nWID / wID 两个键**，老那套子节点一个都不在了，
连 `wID.msgType` 都从 1 变成 3。发老格式换回来的 token 只有 132 字符，
浏览器是 164/172，长度对不上就是这么来的。

nWID 里 canvas.crc32 / webgl.* / audio.audioFp / math.* 必须真实渲染或依赖
JS 引擎浮点格式化，所以按项目里 dtrait 的老办法：**抓一次存成设备档案**
（`utils/mstoken_profile.json`），每次只重算会变的部分。
"""

import json
import os
import random
import time
import uuid

CUSTOM = "Dkdpgh4ZKsQB80/Mfvw36XI1R25+WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe"

_PROFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "mstoken_profile.json")
_profile_cache = None


def _load_profile():
    global _profile_cache
    if _profile_cache is None:
        with open(_PROFILE_PATH, encoding="utf-8") as f:
            _profile_cache = json.load(f)
    return _profile_cache


def rc4(key, data):
    S = list(range(256)); j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) & 255
        S[i], S[j] = S[j], S[i]
    out = bytearray(); i = j = 0
    for b in data:
        i = (i + 1) & 255; j = (j + S[i]) & 255
        S[i], S[j] = S[j], S[i]
        out.append(b ^ S[(S[i] + S[j]) & 255])
    return bytes(out)


def b64_custom_encode(data):
    out = []
    n = len(data)
    for i in range(0, n, 3):
        chunk = data[i:i + 3]
        b0 = chunk[0]
        b1 = chunk[1] if len(chunk) > 1 else 0
        b2 = chunk[2] if len(chunk) > 2 else 0
        trip = (b0 << 16) | (b1 << 8) | b2
        out.append(CUSTOM[(trip >> 18) & 63])
        out.append(CUSTOM[(trip >> 12) & 63])
        out.append(CUSTOM[(trip >> 6) & 63] if len(chunk) > 1 else "=")
        out.append(CUSTOM[trip & 63] if len(chunk) > 2 else "=")
    return "".join(out)


def encode_strdata(plaintext_bytes, nonce):
    cipher = rc4(bytes([nonce]), plaintext_bytes)
    raw = bytes([0x41, nonce]) + cipher
    return b64_custom_encode(raw)


def build_fingerprint(aid=6383, page_id=6241,
                      fixed_uuid=None, fixed_collect_time=None):
    """按实录的新协议拼上报体明文。

    设备常量从 `mstoken_profile.json` 取，会变的三样现算：
    - `custom.uuid`：每次上报一个新的 v4
    - `custom.collectTime`：采集耗时（浮点毫秒）
    - `screen` / `hardwareConcurrency`：跟 `utils/fingerprint` 的档案对齐，
      免得这里报 2560x1440、query 里报别的
    """
    from utils.fingerprint import get_profile
    prof = get_profile()
    data = _load_profile()
    n = json.loads(json.dumps(data["nWID"]))     # 深拷贝，别污染缓存

    w, h = int(prof["screen_width"]), int(prof["screen_height"])
    g = prof["geo"]
    n["screen"].update({
        "height": h, "width": w,
        "availHeight": g[5], "availWidth": g[4],
        "availTop": 0, "availLeft": 0,
    })
    n["navigator"]["hardwareConcurrency"] = int(prof["cpu_core_num"])
    n["navigator"]["userAgent"] = prof["ua"]
    n["webgl"]["renderer"] = prof["webgl_renderer"]
    n["webgl"]["vendor"] = prof["webgl_vendor"]
    # Current Chrome 151's /web/r/token capture reports the page AudioContext
    # as running (the older suspended value was from a stale bootstrap
    # fixture).  Keep this aligned with the active browser profile; the
    # field is inside encrypted strData, so the four-byte spelling difference
    # changes the whole request body.
    audio = n.get("audio") or {}
    audio_context = audio.get("audioContext") or {}
    audio_context["state"] = "running"
    audio["audioContext"] = audio_context
    n["audio"] = audio

    # In Chrome's object insertion order ``custom`` precedes ``ms_version``.
    # Pop/reinsert the latter so the serialized plaintext has the same key
    # order (JSON key order is part of the encrypted report bytes).
    ms_version = n.pop("ms_version", "0.0.0.1")
    if fixed_uuid is None:
        fixed_uuid = os.getenv("DY_MSTOKEN_FIXED_UUID") or str(uuid.uuid4())
    if fixed_collect_time is None:
        fixed_collect_time = os.getenv("DY_MSTOKEN_FIXED_COLLECT_TIME")
    if fixed_collect_time is None:
        fixed_collect_time = round(random.uniform(40, 160), 10)
    else:
        try:
            # json.loads preserves integer/float spelling supplied by a debug
            # fixture while still accepting a plain numeric environment value.
            fixed_collect_time = json.loads(str(fixed_collect_time))
        except (TypeError, ValueError, json.JSONDecodeError):
            fixed_collect_time = float(fixed_collect_time)
    n["custom"] = json.dumps({
        "version": ms_version,
        "fxgDid": "",
        "uuid": fixed_uuid,
        # Chrome reports a JS performance.now() delta, including long-tail
        # floating-point values; keep it configurable for deterministic replay.
        "collectTime": fixed_collect_time,
        "aid": aid,
        "pageId": page_id,
    }, ensure_ascii=False, separators=(",", ":"))
    n["ms_version"] = ms_version

    return json.dumps({"nWID": n, "wID": data["wID"]},
                      ensure_ascii=False, separators=(",", ":"))


def build_report_body(aid=6383, page_id=6241, fixed_nonce=None,
                      fixed_timestamp=None, fixed_uuid=None,
                      fixed_collect_time=None):
    plaintext = build_fingerprint(
        aid=aid, page_id=page_id, fixed_uuid=fixed_uuid,
        fixed_collect_time=fixed_collect_time)
    if fixed_nonce is None:
        fixed_nonce = os.getenv("DY_MSTOKEN_FIXED_NONCE")
    nonce = random.randint(0, 255) if fixed_nonce is None else int(fixed_nonce)
    if fixed_timestamp is None:
        fixed_timestamp = os.getenv("DY_MSTOKEN_FIXED_TIMESTAMP")
    timestamp = int(time.time() * 1000) if fixed_timestamp is None else int(fixed_timestamp)
    strData = encode_strdata(plaintext.encode("utf-8"), nonce)
    envelope = {"magic": 538969122, "version": 1, "dataType": 8,
                "strData": strData, "tspFromClient": int(time.time() * 1000), "ulr": 0}
    envelope["tspFromClient"] = timestamp
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
