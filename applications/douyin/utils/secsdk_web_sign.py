# -*- coding: utf-8 -*-
"""抖音主站 x-secsdk-web-signature 纯算实现。

算法来源与实测依据
------------------
逆向路径（2026-08-16）：
  1. CDP hook `XMLHttpRequest.prototype.open` 抓调用栈，定位到
       https://lf-security.bytegoofy.com/obj/security-secsdk/runtime_bundler_34.js
     第 17 行 col 119759：
       `l = window.use("webSignUrl"); e.payload.args[1] = l(k).url`
     即该 SDK 的 webSign 策略把整条 URL 交给 `webSignUrl` 重写成带签名的 URL。
  2. `webSignUrl` 本体是 VMP 字节码解释器（stack VM，opcode 63 负责外部调用），
     静态读不动；但它最终调的是 `window.CryptoJS.MD5`，于是在 MD5 入口 hook
     直接读到被哈希的明文（与项目逆 passport sign 时用的是同一招）。
  3. 明文格式（实测）：
       {uifid}_{timestamp}_{CONST}_{canonical_query}
     CONST = A96D855A08C0A9707F8BEF0D9A527E4E
     经探针确认为 VM 常量池里的写死盐：换 uifid、清空全部 cookie 后仍不变，
     且不出现在 cookie / localStorage / 策略配置中。
  4. MD5 十六进制小写（32 位）即 x-secsdk-web-signature。

canonical_query 规范化规则（逐字符探针实测）
  - 保持原始参数顺序，不排序；重复参数原样保留。
  - 每个 value 先解码（`+`→空格、`%XX`→字节）再用 encodeURIComponent 重编码：
      不编码 `A-Za-z0-9 - _ . ! ~ * ' ( )`，其余编码，空格→`%20`，中文→UTF-8。
      故 `a+b`→`a%20b`、`a;b`→`a%3Bb`、`%7E`→`~`、`100%`→`100%25`。
  - key 只解码、不重编码（`a b=1` 原样签成 `a b=1`）。
  - 无等号的裸参数补成 `k=`。
  - timestamp 以 `&timestamp={ts}` 追加到 query 末尾后再参与哈希。
  - query 里没有 uifid 时，SDK 会从 UIFID cookie 取值追加到末尾再签
    （对应本模块的 `uifid` 参数）。

**注意**：签名是对规范化后的 query 算的，服务端也按收到的 query 校验，
所以必须发送 `sign_url()` 返回的 URL，不能拿原始 URL 直接发。

回归验证
  - 抓包 5 条真实样本（mix/listcollection、aweme/favorite、aweme/post、
    aweme/detail、aweme/listcollection）逐字节命中 5/5。
  - 与浏览器内 `webSignUrl` oracle 差分对拍 20+ 组边界用例（百分号编码、
    空格、分号、中文、空值、无等号、无 uifid、重复参数等）全部一致。

受保护接口（来自 SDK 的 webSign 策略配置，www.douyin.com）
  GET : /aweme/v1/web/aweme/detail/、/aweme/v1/web/aweme/post/、
        /aweme/v1/web/aweme/favorite/、/aweme/v1/web/aweme/listcollection/、
        /aweme/v1/web/mix/aweme/、/aweme/v1/web/tab/feed/、
        /aweme/v1/web/mix/list/、/aweme/v1/web/music/aweme/、
        /aweme/v1/web/music/list/、/aweme/v1/web/mix/detail/、
        /aweme/v1/web/mix/listcollection/、/aweme/v1/web/music/detail/、
        /aweme/v1/web/collects/list/、/aweme/v1/web/collects/video/list/
  POST: 前 6 条同上
"""

import hashlib
import time
import urllib.parse

# VM 常量池里的固定盐（与账号 / 会话无关，已探针确认）
WEBSIGN_CONST = "A96D855A08C0A9707F8BEF0D9A527E4E"

# 走签名的接口路径，供调用方判断是否需要加签
PROTECTED_PATHS_GET = (
    "/aweme/v1/web/aweme/detail/",
    "/aweme/v1/web/aweme/post/",
    "/aweme/v1/web/aweme/favorite/",
    "/aweme/v1/web/aweme/listcollection/",
    "/aweme/v1/web/mix/aweme/",
    "/aweme/v1/web/tab/feed/",
    "/aweme/v1/web/mix/list/",
    "/aweme/v1/web/music/aweme/",
    "/aweme/v1/web/music/list/",
    "/aweme/v1/web/mix/detail/",
    "/aweme/v1/web/mix/listcollection/",
    "/aweme/v1/web/music/detail/",
    "/aweme/v1/web/collects/list/",
    "/aweme/v1/web/collects/video/list/",
)
PROTECTED_PATHS_POST = (
    "/aweme/v1/web/aweme/detail/",
    "/aweme/v1/web/aweme/post/",
    "/aweme/v1/web/aweme/favorite/",
    "/aweme/v1/web/aweme/listcollection/",
    "/aweme/v1/web/mix/aweme/",
    "/aweme/v1/web/tab/feed/",
)

# encodeURIComponent 的非编码集合。Python 的 quote 本身就不编码 A-Za-z0-9_.-~，
# 这里补上 !*'() 即与 JS 完全等价。
_ENCODE_SAFE = "!*'()"

_SIG_KEY = "x-secsdk-web-signature"
_TS_KEY = "timestamp"


def _encode_component(value):
    """等价于 JS 的 encodeURIComponent。"""
    return urllib.parse.quote(str(value), safe=_ENCODE_SAFE, encoding="utf-8")


def canonical_query(query):
    """按 SDK 规则规范化 query 字符串（顺序不变，value 重编码，key 只解码）。"""
    parts = []
    for pair in query.split("&"):
        if not pair:
            continue
        if "=" in pair:
            key, value = pair.split("=", 1)
        else:
            key, value = pair, ""
        key = urllib.parse.unquote_plus(key, encoding="utf-8", errors="replace")
        value = urllib.parse.unquote_plus(value, encoding="utf-8", errors="replace")
        parts.append("%s=%s" % (key, _encode_component(value)))
    return "&".join(parts)


def _split(url):
    """拆成 (base, query)，并剥掉已有的 timestamp / 签名字段，保证幂等。"""
    base, _, query = url.partition("?")
    kept = []
    for pair in query.split("&"):
        if not pair:
            continue
        name = pair.split("=", 1)[0]
        if name in (_TS_KEY, _SIG_KEY):
            continue
        kept.append(pair)
    return base, "&".join(kept)


def sign(url, ts=None, uifid=None):
    """计算 x-secsdk-web-signature。

    :param url:   完整 API URL（scheme+host+path+query）。已带 timestamp /
                  x-secsdk-web-signature 时会自动剥除，可重复调用。
    :param ts:    10 位秒级时间戳，None 时取当前时间。
    :param uifid: query 里没有 uifid 时的兜底值（对应 UIFID cookie）。
    :returns:     (ts, signature, signed_query)
                  signature 为 32 位小写 hex；
                  signed_query 是**实际应当发送**的规范化 query（含 timestamp，
                  不含签名字段）。
    """
    if ts is None:
        ts = int(time.time())
    ts = int(ts)

    _, raw_query = _split(url)
    canon = canonical_query(raw_query)

    # query 里没有 uifid 时，SDK 会从 UIFID cookie 取值追加到末尾
    names = [p.split("=", 1)[0] for p in canon.split("&") if p]
    if "uifid" not in names and uifid:
        canon = ("%s&uifid=%s" % (canon, _encode_component(uifid))
                 if canon else "uifid=%s" % _encode_component(uifid))

    signed_query = ("%s&%s=%d" % (canon, _TS_KEY, ts)) if canon \
        else "%s=%d" % (_TS_KEY, ts)

    # 取参与拼明文的 uifid（解码后的原值）
    uifid_value = ""
    for pair in signed_query.split("&"):
        if pair.startswith("uifid="):
            uifid_value = urllib.parse.unquote_plus(pair[6:], encoding="utf-8",
                                                    errors="replace")
            break
    if not uifid_value and uifid:
        uifid_value = uifid

    plain = "%s_%d_%s_%s" % (uifid_value, ts, WEBSIGN_CONST, signed_query)
    signature = hashlib.md5(plain.encode("utf-8")).hexdigest()
    return ts, signature, signed_query


def sign_url(url, ts=None, uifid=None):
    """返回可直接发送的完整 URL（规范化 query + timestamp + 签名）。

    服务端按收到的 query 校验签名，所以必须发这个返回值，
    不能拿原始 URL 直接发。
    """
    base, _ = _split(url)
    ts, signature, signed_query = sign(url, ts=ts, uifid=uifid)
    return "%s?%s&%s=%s" % (base, signed_query, _SIG_KEY, signature)


def is_protected(path, method="GET"):
    """判断该 path 是否需要加签（依据 SDK 的 webSign 策略配置）。"""
    table = PROTECTED_PATHS_POST if str(method).upper() == "POST" \
        else PROTECTED_PATHS_GET
    return path in table
