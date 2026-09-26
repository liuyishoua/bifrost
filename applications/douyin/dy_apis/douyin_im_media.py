# -*- coding: utf-8 -*-
"""PC IM 富媒体上传。

抖音 PC IM 的图片/视频/文件上传与 creator 发布链路不同：IM 先请求
``/aweme/v1/web/im/upload/config/v2`` 取得四组短期 STS，然后使用 VOD
``ApplyUploadInner``/TOS/``CommitUploadInner``。本模块只负责上传和把
Commit 结果转换成 IM message content；鉴权与 protobuf 发送仍由
``DouyinAPI`` 完成。

所有 STS、session key 和加密密钥只保存在内存中，不写日志、不落盘。
"""

import json
import os
import re
import tempfile
import time
import urllib.parse

from utils import http_client as requests
requests.packages.urllib3.disable_warnings()
from loguru import logger

from builder.header import HeaderBuilder, HeaderType
from builder.params import Params
from utils.dy_util import splice_url, generate_a_bogus
from utils.imagex_sign import IMAGEX_REGION, VOD_HOST, VOD_SERVICE, sign_request

from dy_apis.douyin_creator_api import (
    _MediaSource,
    _crc32_hex,
    _image_size,
    _slice_size_for,
)


IM_ORIGIN = "https://www.douyin.com"
IM_REFERER = "https://www.douyin.com/chat?isPopup=1"
UPLOAD_CONFIG_PATH = "/aweme/v1/web/im/upload/config/v2"
VOD_API_VERSION = "2020-11-19"
DIRECT_UPLOAD_LIMIT = 3 * 1024 * 1024
MAX_IM_FILE_SIZE = 10 * 1024 * 1024


def _first(value, default=None):
    """Return the first item from a list-like value."""
    if isinstance(value, (list, tuple)):
        return value[0] if value else default
    return value if value is not None else default


def _url_object(value, *, width=0, height=0, data_size=None):
    """Normalize a Douyin image/cover value to the IM card URL shape."""
    if isinstance(value, dict):
        obj = dict(value)
        urls = obj.get("url_list") or obj.get("urlList") or obj.get("urls") or []
        if isinstance(urls, str):
            urls = [urls]
        else:
            urls = list(urls or [])
        uri = obj.get("uri") or obj.get("url") or (urls[0] if urls else "")
        obj["uri"] = uri or ""
        obj["url_list"] = urls or ([uri] if uri else [])
        if width and not obj.get("width"):
            obj["width"] = int(width)
        if height and not obj.get("height"):
            obj["height"] = int(height)
        if data_size is not None and "data_size" not in obj:
            obj["data_size"] = int(data_size)
        return obj
    if isinstance(value, (list, tuple)):
        # A list of image objects is a common aweme detail representation;
        # callers wanting all images should normalize each element themselves.
        value = _first(value, "")
    if value is None:
        value = ""
    value = str(value)
    obj = {"uri": value, "url_list": [value] if value else []}
    if width:
        obj["width"] = int(width)
    if height:
        obj["height"] = int(height)
    if data_size is not None:
        obj["data_size"] = int(data_size)
    return obj


def _detail_from_content(content):
    """Unwrap common get_work_info/aweme detail response shapes."""
    if not isinstance(content, dict):
        return content
    for key in ("aweme_detail", "detail"):
        value = content.get(key)
        if isinstance(value, dict):
            return value
    return content


def _is_card_payload(content, *, photos=False):
    if not isinstance(content, dict) or not content.get("itemId"):
        return False
    if photos:
        return content.get("awemeType") == 68 or content.get("aweType") == 0
    return content.get("aweType") == 800 or content.get("awemeType") == 0


def _author_values(detail):
    author = detail.get("author") or detail.get("user") or {}
    if not isinstance(author, dict):
        author = {}
    uid = (author.get("uid") or author.get("user_id") or detail.get("uid")
           or detail.get("profile_uid") or "")
    sec_uid = (author.get("sec_uid") or author.get("sec_user_id")
               or detail.get("secUID") or detail.get("sec_uid") or "")
    name = (author.get("nickname") or author.get("name")
            or detail.get("content_name") or "")
    return str(uid or ""), str(sec_uid or ""), str(name or "")


def _detail_cover(detail, *, photos=False):
    video = detail.get("video") or {}
    if not isinstance(video, dict):
        video = {}
    if photos:
        images = (detail.get("images") or detail.get("image_list")
                  or detail.get("image_infos") or [])
        first_image = _first(images, {})
        if not isinstance(first_image, dict):
            first_image = {"url_list": first_image}
        value = (first_image.get("display_image") or first_image.get("cover")
                 or first_image)
        width = first_image.get("width") or detail.get("cover_width") or 0
        height = first_image.get("height") or detail.get("cover_height") or 0
    else:
        cover = video.get("cover") or video.get("origin_cover") or {}
        value = cover or detail.get("cover_url") or detail.get("cover") or ""
        width = (video.get("width") or (cover.get("width") if isinstance(cover, dict) else 0)
                 or detail.get("cover_width") or 0)
        height = (video.get("height") or (cover.get("height") if isinstance(cover, dict) else 0)
                  or detail.get("cover_height") or 0)
    if not width and isinstance(value, dict):
        width = value.get("width") or 0
    if not height and isinstance(value, dict):
        height = value.get("height") or 0
    return _url_object(value, width=width, height=height), int(width or 0), int(height or 0)


def _item_id_from_value(value):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if value.isdigit():
        return value
    match = re.search(r"/(?:video|note|slides)/(\d+)", value)
    if match:
        return match.group(1)
    match = re.search(r"[?&]modal_id=(\d+)", value)
    return match.group(1) if match else ""


def _request(auth, method, url, **kwargs):
    """Use Auth's persistent session only for same-origin IM requests."""
    host = urllib.parse.urlsplit(url).hostname or ""
    if auth is not None and hasattr(auth, "request") and host == "www.douyin.com":
        # Auth.request owns the persistent CookieJar; passing ``cookies=`` as
        # well can duplicate stale values in curl_cffi's session.
        kwargs.pop("cookies", None)
        return auth.request(method, url, **kwargs)
    return requests.request(method, url, **kwargs)


def _read_bytes(src):
    if isinstance(src, (bytes, bytearray, memoryview)):
        return bytes(src)
    if isinstance(src, str) and src.startswith(("http://", "https://")):
        resp = requests.get(src, verify=False, timeout=120)
        resp.raise_for_status()
        return resp.content
    with open(src, "rb") as fp:
        return fp.read()


def _source_name(src, default="file.bin"):
    if isinstance(src, str) and not src.startswith(("http://", "https://")):
        return os.path.basename(src) or default
    return default


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _sts_value(cfg, *names):
    for name in names:
        value = cfg.get(name)
        if value:
            return str(value)
    return ""


def _normalize_sts(cfg):
    cfg = dict(cfg or {})
    result = {
        "AccessKeyID": _sts_value(cfg, "access_key_id", "AccessKeyID", "AccessKeyId"),
        "SecretAccessKey": _sts_value(cfg, "secret_access_key", "SecretAccessKey"),
        "SessionToken": _sts_value(cfg, "session_token", "SessionToken"),
        "space_name": _sts_value(cfg, "space_name", "SpaceName"),
        "expire_at": _as_int(cfg.get("expire_at") or cfg.get("ExpiredTime")),
    }
    if not all(result[key] for key in ("AccessKeyID", "SecretAccessKey", "SessionToken", "space_name")):
        raise RuntimeError(f"IM 上传凭证字段不完整: {sorted(cfg.keys())}")
    return result


def _sts_from_config(config, name):
    value = config.get(name) or {}
    return value if value.get("AccessKeyID") else _normalize_sts(value)


def _gateway_headers(sign_headers, content_type=None):
    headers = {
        "accept": "*/*",
        "accept-language": HeaderBuilder.accept_language,
        "origin": IM_ORIGIN,
        "referer": f"{IM_ORIGIN}/",
        "user-agent": HeaderBuilder.ua,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cross-site",
    }
    headers.update(sign_headers)
    if content_type:
        headers["content-type"] = content_type
    return headers


def _tos_headers(node, *, user_id="", crc32=None):
    headers = {
        "authorization": node["auth"],
        "referer": f"{IM_ORIGIN}/",
        "user-agent": HeaderBuilder.ua,
        "x-storage-u": urllib.parse.quote(str(user_id or "")),
        "content-type": "application/octet-stream",
        "accept": "*/*",
        "accept-language": HeaderBuilder.accept_language,
        "origin": IM_ORIGIN,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cross-site",
    }
    if crc32 is not None:
        headers["content-crc32"] = crc32
    headers.update(node.get("upload_header") or {})
    return headers


def _tos_post(auth, url, headers, data=None, *, proxies=None, timeout=300):
    resp = _request(
        auth, "POST", url, headers=headers, data=data, verify=False,
        proxies=proxies, timeout=timeout,
    )
    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError(f"IM TOS 返回非 JSON（HTTP {resp.status_code}）") from exc
    # TOS uses 2000 for upload/part operations. A few gateway versions omit
    # code on an otherwise successful empty response, so accept HTTP 2xx too.
    if payload.get("code") not in (None, 2000, "2000"):
        raise RuntimeError(f"IM TOS 上传失败: {payload}")
    return payload


def _result_item(payload):
    result = (payload or {}).get("Result") or {}
    items = result.get("Results") or []
    return (items[0] if items else result), result


class DouyinIMMedia:
    """PC IM media uploader and content builders."""

    @staticmethod
    def _resolve_user_id(auth, user_id=""):
        if user_id:
            return str(user_id)
        try:
            return str(auth.get_uid())
        except Exception:
            return ""

    @staticmethod
    def get_upload_config(auth, *, force=False, proxies=None,
                          referer=IM_REFERER) -> dict:
        cached = getattr(auth, "_im_upload_config", None)
        if cached and not force:
            # expire_at is milliseconds in the web response; tolerate seconds.
            expires = max(
                _as_int((cached.get("public_image_config") or {}).get("expire_at")),
                _as_int((cached.get("public_file_config") or {}).get("expire_at")),
            )
            now = int(__import__("time").time() * (1000 if expires > 10**11 else 1))
            if expires and expires - now > 30:
                return cached

        headers = HeaderBuilder.build(HeaderType.GET)
        headers.set_referer(referer)
        # Config is a same-origin XHR. Current PC IM sends bd-ticket-guard;
        # keep compatibility with lightweight fake Auth objects used by tests.
        if hasattr(headers, "with_bd") and hasattr(auth, "ticket_matches_session"):
            headers.with_bd(UPLOAD_CONFIG_PATH, auth)
        params = Params()
        try:
            params.with_platform(auth=auth, url=referer)
        except Exception:
            # A freshly-created Auth may not have a webid yet. The endpoint
            # still accepts the regular browser identity fields below.
            params = Params().with_platform()
            params.with_verify_fp(auth)
        params.add_param("msToken", auth.msToken)
        params.add_param(
            "a_bogus", generate_a_bogus(splice_url(params.get()), host="www.douyin.com")
        )
        resp = _request(
            auth, "GET", f"{IM_ORIGIN}{UPLOAD_CONFIG_PATH}",
            headers=headers.get(), params=params.get(), cookies=getattr(auth, "cookie", None),
            verify=False, proxies=proxies,
        )
        try:
            payload = resp.json()
        except Exception as exc:
            raise RuntimeError("IM 上传配置返回不可解析响应") from exc
        if payload.get("status_code") not in (0, None):
            raise RuntimeError(f"获取 IM 上传配置失败: {payload}")
        required = ("public_image_config", "inner_image_config", "public_file_config")
        if any(not payload.get(name) for name in required):
            raise RuntimeError(f"获取 IM 上传配置字段缺失: {payload.keys()}")
        config = dict(payload)
        for name in required + ("public_image_config_v2",):
            if config.get(name):
                config[name] = _normalize_sts(config[name])
        try:
            auth._im_upload_config = config
        except Exception:
            pass
        return config

    @staticmethod
    def _apply_upload(auth, sts, file_type, size, *, user_id="", gcm=False,
                       proxies=None):
        query = {
            "Action": "ApplyUploadInner",
            "Version": VOD_API_VERSION,
            "SpaceName": sts["space_name"],
            "FileType": file_type,
            "IsInner": 1,
            "NeedFallback": "true",
            "FileSize": int(size),
        }
        if gcm:
            query["OpenGcmEnc"] = "true"
        signed = sign_request(
            sts["AccessKeyID"], sts["SecretAccessKey"], sts["SessionToken"],
            method="GET", query_params=query, body=b"", service=VOD_SERVICE,
            region=IMAGEX_REGION,
        )
        resp = _request(
            auth, "GET", f"https://{VOD_HOST}/", headers=_gateway_headers(signed),
            params=query, verify=False, proxies=proxies,
        )
        payload = resp.json()
        result = payload.get("Result") or {}
        nodes = (result.get("InnerUploadAddress") or {}).get("UploadNodes") or []
        if not nodes:
            raise RuntimeError(f"ApplyUploadInner({file_type}) 失败: {payload}")
        node = nodes[0]
        stores = node.get("StoreInfos") or []
        if not stores:
            raise RuntimeError(f"ApplyUploadInner({file_type}) 缺少 StoreInfos")
        store = stores[0]
        return {
            "store_uri": store.get("StoreUri") or "",
            "auth": store.get("Auth") or "",
            "upload_id": store.get("UploadID") or "",
            "upload_host": node.get("UploadHost") or "",
            "session_key": node.get("SessionKey") or "",
            "upload_header": node.get("UploadHeader") or {},
        }

    @staticmethod
    def _upload_source(auth, node, source, *, user_id="", proxies=None):
        if source.size <= DIRECT_UPLOAD_LIMIT:
            data = source.read()
            url = f"https://{node['upload_host']}/upload/v1/{node['store_uri']}"
            return _tos_post(
                auth, url, _tos_headers(node, user_id=user_id, crc32=_crc32_hex(data)),
                data=data, proxies=proxies,
            )

        base = f"https://{node['upload_host']}/upload/v1/{node['store_uri']}"
        upload_id = node.get("upload_id")
        if not upload_id:
            init = _tos_post(
                auth, f"{base}?uploadmode=part&phase=init",
                _tos_headers(node, user_id=user_id), proxies=proxies,
            )
            upload_id = (init.get("data") or {}).get("uploadid")
            if not upload_id:
                raise RuntimeError(f"IM 分片初始化失败: {init}")
        part_size = max(_slice_size_for(source.size), 5 * 1024 * 1024)
        crc_list = []
        offset = 0
        part_no = 1
        while offset < source.size:
            chunk = source.read(offset, part_size)
            if not chunk:
                raise RuntimeError("IM 分片读取到空数据")
            crc = _crc32_hex(chunk)
            _tos_post(
                auth,
                f"{base}?uploadid={urllib.parse.quote(str(upload_id))}"
                f"&part_number={part_no}&phase=transfer&part_offset={offset}",
                _tos_headers(node, user_id=user_id, crc32=crc), data=chunk,
                proxies=proxies,
            )
            crc_list.append(crc)
            offset += len(chunk)
            part_no += 1
        merge = ",".join(f"{i + 1}:{crc}" for i, crc in enumerate(crc_list))
        return _tos_post(
            auth,
            f"{base}?uploadmode=part&phase=finish&uploadid={urllib.parse.quote(str(upload_id))}",
            _tos_headers(node, user_id=user_id), data=merge.encode(), proxies=proxies,
        )

    @staticmethod
    def _commit_upload(auth, sts, node, *, process_action=None, user_id="",
                       proxies=None):
        body_obj = {"SessionKey": node["session_key"], "Functions": process_action or []}
        body = json.dumps(body_obj, ensure_ascii=False, separators=(",", ":")).encode()
        query = {
            "Action": "CommitUploadInner",
            "Version": VOD_API_VERSION,
            "SpaceName": sts["space_name"],
        }
        signed = sign_request(
            sts["AccessKeyID"], sts["SecretAccessKey"], sts["SessionToken"],
            method="POST", query_params=query, body=body, service=VOD_SERVICE,
            region=IMAGEX_REGION,
        )
        resp = _request(
            auth, "POST", f"https://{VOD_HOST}/", headers=_gateway_headers(
                signed, "text/plain;charset=UTF-8"
            ), params=query, data=body, verify=False, proxies=proxies,
        )
        payload = resp.json()
        item, result = _result_item(payload)
        if not item:
            raise RuntimeError(f"CommitUploadInner 失败: {payload}")
        return item

    @staticmethod
    def _encryption(item):
        return (item or {}).get("Encryption") or {}

    @staticmethod
    def _image_content(item, data, *, gif=False):
        enc = DouyinIMMedia._encryption(item)
        extra = enc.get("Extra") or {}
        uri = enc.get("Uri") or item.get("Uri") or ""
        md5 = enc.get("SourceMd5") or item.get("SourceMd5") or ""
        secret = enc.get("SecretKey") or item.get("SecretKey") or ""
        width, height = _image_size(data)
        width = _as_int(extra.get("img_width"), width)
        height = _as_int(extra.get("img_height"), height)
        return {
            "resource_url": {
                "oid": uri,
                "skey": secret,
                "data_size": _as_int(extra.get("img_size"), len(data)),
                "md5": md5,
            },
            "cover_height": height,
            "cover_width": width,
            "check_pics": [],
            "md5": md5,
            "from_gallery": 1,
            "aweType": 2703 if gif else 2702,
        }

    @staticmethod
    def _plain_uri(item):
        enc = DouyinIMMedia._encryption(item)
        return enc.get("Uri") or item.get("Uri") or item.get("uri") or ""

    @staticmethod
    def upload_image(auth, image, *, user_id="", gif=None, proxies=None) -> dict:
        user_id = DouyinIMMedia._resolve_user_id(auth, user_id)
        data = _read_bytes(image)
        if gif is None:
            gif = _source_name(image).lower().endswith(".gif")
        config = DouyinIMMedia.get_upload_config(auth, proxies=proxies)
        sts = _sts_from_config(config, "public_image_config")
        policy = {"policy-set": "still", "still-width": "480", "still-height": "480"} \
            if gif else {"policy-set": "check,thumb,medium,large"}
        action = [{
            "name": "Encryption",
            "input": {"Config": {"copies": "cipher_v2"}},
            "PolicyParams": policy,
        }]
        node = DouyinIMMedia._apply_upload(
            auth, sts, "image", len(data), user_id=user_id, proxies=proxies,
        )
        DouyinIMMedia._upload_source(
            auth, node, _MediaSource(data), user_id=user_id, proxies=proxies,
        )
        item = DouyinIMMedia._commit_upload(
            auth, sts, node, process_action=action, user_id=user_id, proxies=proxies,
        )
        return DouyinIMMedia._image_content(item, data, gif=gif)

    @staticmethod
    def _video_cover(video, thumb=None):
        if thumb is not None:
            return _read_bytes(thumb), _source_name(thumb, "cover.jpg")
        # Match the browser's required imageBlob by extracting the first frame
        # when OpenCV is available. Callers can always pass an explicit thumb.
        try:
            import cv2
            source = _MediaSource(video)
            if source._path is not None:
                path = os.fspath(source._path)
                cleanup = None
            else:
                fd, path = tempfile.mkstemp(prefix="douyin_im_", suffix=".mp4")
                os.close(fd)
                with open(path, "wb") as fp:
                    fp.write(source.read())
                cleanup = path
            cap = cv2.VideoCapture(path)
            ok, frame = cap.read()
            cap.release()
            if cleanup:
                try:
                    os.unlink(cleanup)
                except OSError:
                    pass
            if not ok:
                raise RuntimeError("无法从视频读取首帧")
            ok, encoded = cv2.imencode(".jpg", frame)
            if not ok:
                raise RuntimeError("视频首帧 JPEG 编码失败")
            return encoded.tobytes(), "cover.jpg"
        except Exception as exc:
            raise ValueError("视频私信需要 thumb/封面图，且自动抽帧不可用") from exc

    @staticmethod
    def upload_video(auth, video, *, thumb=None, user_id="", proxies=None) -> dict:
        user_id = DouyinIMMedia._resolve_user_id(auth, user_id)
        video_source = _MediaSource(video)
        cover_data, _cover_name = DouyinIMMedia._video_cover(video, thumb)
        config = DouyinIMMedia.get_upload_config(auth, proxies=proxies)
        public_sts = _sts_from_config(config, "public_image_config")
        # PC IM's getCheckPicUploader uses ``inner_image_config.space_name``
        # but signs it with the STS fields from ``public_image_config``.
        # Keep that slightly surprising browser behaviour byte-for-byte.
        inner_cfg = _sts_from_config(config, "inner_image_config")
        inner_sts = dict(public_sts)
        inner_sts["space_name"] = inner_cfg["space_name"]

        # The cover uses the inner image space without Encryption and becomes
        # the ``check_pics`` URI in the final message.
        cover_node = DouyinIMMedia._apply_upload(
            auth, inner_sts, "image", len(cover_data), user_id=user_id, proxies=proxies,
        )
        DouyinIMMedia._upload_source(
            auth, cover_node, _MediaSource(cover_data), user_id=user_id, proxies=proxies,
        )
        cover_item = DouyinIMMedia._commit_upload(
            auth, inner_sts, cover_node, user_id=user_id, proxies=proxies,
        )
        cover_uri = DouyinIMMedia._plain_uri(cover_item)

        action = [{
            "name": "Encryption",
            "input": {"Config": {"copies": "cipher_v2", "aes_chunk_size": "524288"}},
            "PolicyParams": {"policy-set": "medium"},
        }]
        video_node = DouyinIMMedia._apply_upload(
            auth, public_sts, "video", video_source.size, user_id=user_id, proxies=proxies,
        )
        DouyinIMMedia._upload_source(
            auth, video_node, video_source, user_id=user_id, proxies=proxies,
        )
        item = DouyinIMMedia._commit_upload(
            auth, public_sts, video_node, process_action=action,
            user_id=user_id, proxies=proxies,
        )
        enc = DouyinIMMedia._encryption(item)
        extra = enc.get("Extra") or {}
        meta = item.get("VideoMeta") or item.get("SourceInfo") or {}
        return {
            "video": {
                "tkey": enc.get("Uri") or item.get("Uri") or "",
                "md5": enc.get("SourceMd5") or item.get("SourceMd5") or "",
                "skey": enc.get("SecretKey") or item.get("SecretKey") or "",
            },
            "poster": {
                "oid": extra.get("thumb_uri") or cover_uri,
                "md5": extra.get("thumb_md5") or "",
                "skey": extra.get("thumb_secret") or "",
            },
            "height": _as_int(meta.get("Height")),
            "width": _as_int(meta.get("Width")),
            "check_pics": [cover_uri] if cover_uri else [],
        }

    @staticmethod
    def upload_audio(auth, audio, *, user_id="", proxies=None) -> dict:
        # The current PC IM uploader exposes only image/video/object. A real
        # ``FileType=audio`` ApplyUploadInner request is rejected with 30402,
        # and this build contains no outgoing voice recorder. Keep this method
        # explicit instead of pretending an arbitrary audio file is a voice
        # message. Callers can pass a payload captured from a compatible client
        # directly to ``DouyinAPI.send_audio(..., content=payload)``.
        if isinstance(audio, dict):
            # Already uploaded/captured payloads can be normalized here for
            # callers that share one media pipeline across platforms.
            return dict(audio)
        raise NotImplementedError(
            "当前 PC IM 网页端没有可复用的语音上传协议（VOD audio 被服务端拒绝）；"
            "请传入兼容客户端捕获的 voice content dict"
        )

    @staticmethod
    def upload_file(auth, file, *, user_id="", proxies=None) -> dict:
        user_id = DouyinIMMedia._resolve_user_id(auth, user_id)
        data = _read_bytes(file)
        if len(data) > MAX_IM_FILE_SIZE:
            raise ValueError("抖音 PC IM 文件附件不能超过 10MB")
        config = DouyinIMMedia.get_upload_config(auth, proxies=proxies)
        sts = _sts_from_config(config, "public_file_config")
        node = DouyinIMMedia._apply_upload(
            auth, sts, "object", len(data), user_id=user_id, gcm=True, proxies=proxies,
        )
        DouyinIMMedia._upload_source(
            auth, node, _MediaSource(data), user_id=user_id, proxies=proxies,
        )
        item = DouyinIMMedia._commit_upload(
            auth, sts, node, user_id=user_id, proxies=proxies,
        )
        enc = DouyinIMMedia._encryption(item)
        name = _source_name(file)
        ext = os.path.splitext(name)[1].lstrip(".").lower()
        return {
            "aweType": 15001,
            "name": name,
            "data_size": len(data),
            "md5": enc.get("SourceMd5") or item.get("SourceMd5") or "",
            "skey": enc.get("SecretKey") or item.get("SecretKey") or "",
            "uri": enc.get("Uri") or item.get("Uri") or "",
            "format": ext,
        }

    @staticmethod
    def build_share_aweme_content(content, *, uid="", sec_uid="", name="",
                                  title="", item_id="", share_id="",
                                  timestamp=None, **extra) -> dict:
        """Build the current PC-IM video-share card (message type 8).

        ``content`` may be a complete historical card, a ``get_work_info``
        response/detail object, or a plain aweme id/URL. Complete cards are
        copied without dropping experiment fields; detail objects are mapped
        to the fields consumed by the current PC IM renderer.
        """
        if _is_card_payload(content):
            payload = dict(content)
            if not payload.get("share_id") and uid and payload.get("itemId"):
                stamp = int(timestamp if timestamp is not None else time.time() * 1000)
                payload["share_id"] = f"{uid}_{stamp}_{payload['itemId']}"
            payload.update(extra)
            return payload
        detail = _detail_from_content(content)
        if not isinstance(detail, dict):
            detail = {}
        author_uid, author_sec_uid, author_name = _author_values(detail)
        uid = str(uid or author_uid or "")
        sec_uid = str(sec_uid or author_sec_uid or "")
        name = str(name or author_name or "")
        item_id = str(item_id or detail.get("aweme_id") or detail.get("itemId")
                      or detail.get("item_id") or _item_id_from_value(content) or "")
        cover, width, height = _detail_cover(detail, photos=False)
        title = str(title or detail.get("desc") or detail.get("title")
                    or detail.get("content_title") or "")
        if not share_id and uid and item_id:
            stamp = int(timestamp if timestamp is not None else time.time() * 1000)
            share_id = f"{uid}_{stamp}_{item_id}"
        ai_ext = detail.get("ai_ext") or "{}"
        if isinstance(ai_ext, (dict, list)):
            ai_ext = json.dumps(ai_ext, ensure_ascii=False, separators=(",", ":"))
        payload = {
            "aweType": 800,
            "awemeType": 0,
            "content_name": name,
            "content_title": title,
            "content_thumb": dict(cover),
            "cover_height": height,
            "cover_url": dict(cover),
            "cover_width": width,
            "itemId": item_id,
            "secUID": sec_uid,
            "uid": uid,
            "share_id": str(share_id or ""),
            "share_with_timestamp": 0,
            "is_aigc": bool(detail.get("is_aigc", False)),
            "is_hot_spot_video": bool(detail.get("is_hot_spot_video", False)),
            "is_live_photo": int(detail.get("is_live_photo", 0) or 0),
            "is_slides": bool(detail.get("is_slides", False)),
            "is_story": bool(detail.get("is_story", False)),
            "is_text": int(detail.get("is_text", 0) or 0),
            "create_id": str(detail.get("create_id") or ""),
            "share_info": detail.get("share_info") or [],
            "anchor_info": detail.get("anchor_info") or {},
            "poi_track_params": detail.get("poi_track_params") or {},
            "ai_ext": ai_ext,
        }
        # Preserve useful fields from a detail response when present, while
        # allowing explicit caller overrides for account/experiment metadata.
        for key in ("profile_uid", "profile_sec_uid", "scene_type", "send_source",
                    "publish_way", "hot_spot_create_time", "ecom_share_track_params"):
            if detail.get(key) is not None:
                payload[key] = detail[key]
        payload.update(extra)
        return payload

    @staticmethod
    def build_share_photos_content(content, *, uid="", sec_uid="", name="",
                                   title="", item_id="", share_id="",
                                   timestamp=None, image_count=None,
                                   image_index=0, **extra) -> dict:
        """Build the current PC-IM photo/text share card (message type 77)."""
        if _is_card_payload(content, photos=True):
            payload = dict(content)
            if not payload.get("share_id") and uid and payload.get("itemId"):
                stamp = int(timestamp if timestamp is not None else time.time() * 1000)
                payload["share_id"] = f"{uid}_{stamp}_{payload['itemId']}"
            payload.update(extra)
            return payload
        detail = _detail_from_content(content)
        if not isinstance(detail, dict):
            detail = {}
        author_uid, author_sec_uid, author_name = _author_values(detail)
        uid = str(uid or author_uid or "")
        sec_uid = str(sec_uid or author_sec_uid or "")
        name = str(name or author_name or "")
        item_id = str(item_id or detail.get("aweme_id") or detail.get("itemId")
                      or detail.get("item_id") or _item_id_from_value(content) or "")
        cover, width, height = _detail_cover(detail, photos=True)
        title = str(title or detail.get("desc") or detail.get("title")
                    or detail.get("content_title") or "")
        images = (detail.get("images") or detail.get("image_list")
                  or detail.get("image_infos") or [])
        if image_count is None:
            image_count = detail.get("image_count") or len(images) or 1
        if not share_id and uid and item_id:
            stamp = int(timestamp if timestamp is not None else time.time() * 1000)
            share_id = f"{uid}_{stamp}_{item_id}"
        ai_ext = detail.get("ai_ext") or "{}"
        if isinstance(ai_ext, (dict, list)):
            ai_ext = json.dumps(ai_ext, ensure_ascii=False, separators=(",", ":"))
        payload = {
            "aweType": 0,
            "awemeType": 68,
            "content_name": name,
            "content_title": title,
            "content_thumb": dict(cover),
            "cover_height": height,
            "cover_url": dict(cover),
            "cover_url_v2": dict(cover),
            "cover_width": width,
            "image_count": int(image_count or 1),
            "image_index": int(image_index or 0),
            "itemId": item_id,
            "secUID": sec_uid,
            "uid": uid,
            "share_id": str(share_id or ""),
            "share_with_timestamp": 0,
            "is_aigc": bool(detail.get("is_aigc", False)),
            "is_hot_spot_video": bool(detail.get("is_hot_spot_video", False)),
            "is_live_photo": int(detail.get("is_live_photo", 0) or 0),
            "is_slides": bool(detail.get("is_slides", False)),
            "is_story": bool(detail.get("is_story", False)),
            "is_text": int(detail.get("is_text", 0) or 0),
            "share_info": detail.get("share_info") or [],
            "anchor_info": detail.get("anchor_info") or {},
            "poi_track_params": detail.get("poi_track_params") or {},
            "ai_ext": ai_ext,
        }
        for key in ("profile_uid", "profile_sec_uid", "scene_type", "send_source",
                    "publish_way", "hot_spot_create_time", "ecom_share_track_params"):
            if detail.get(key) is not None:
                payload[key] = detail[key]
        payload.update(extra)
        return payload

    @staticmethod
    def build_share_web_content(content, *, title="", desc="", cover_url="",
                                link_url="", **extra) -> dict:
        """Build a web/link card (message type 26).

        The PC renderer intentionally ignores a plain URL: ``link_url`` must
        contain a valid ``pc_iframe_src`` query parameter. If absent, the
        original URL is used as that iframe target.
        """
        if isinstance(content, dict):
            payload = dict(content)
            target = str(payload.get("link_url") or link_url or "")
            if title:
                payload["title"] = title
            if desc:
                payload["desc"] = desc
            if cover_url:
                payload["cover_url"] = cover_url
        else:
            payload = {}
            target = str(link_url or content or "")
        if target:
            parsed = urllib.parse.urlsplit(target)
            query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if not any(key == "pc_iframe_src" and value for key, value in query):
                query.append(("pc_iframe_src", target))
                target = urllib.parse.urlunsplit((
                    parsed.scheme, parsed.netloc, parsed.path,
                    urllib.parse.urlencode(query), parsed.fragment,
                ))
        payload.setdefault("link_url", target)
        payload.setdefault("cover_url", str(cover_url or ""))
        payload.setdefault("title", str(title or ""))
        payload.setdefault("desc", str(desc or ""))
        payload["link_url"] = target
        payload.update(extra)
        return payload

    @staticmethod
    def build_user_card_content(content=None, *, uid="", sec_uid="", name="",
                                avatar=None, cover_items=None, cover_url=None,
                                **extra) -> dict:
        """Build a user-share card (message type 25)."""
        if isinstance(content, dict):
            source = dict(content)
        else:
            source = {}
        uid = str(uid or source.get("uid") or source.get("user_id") or "")
        sec_uid = str(sec_uid or source.get("secUID") or source.get("sec_uid")
                      or source.get("sec_user_id") or "")
        name = str(name or source.get("name") or source.get("nickname") or "")
        avatar = avatar if avatar is not None else source.get("avatar")
        if avatar is None:
            avatar = source.get("avatar_larger") or source.get("avatar_thumb") or ""
        covers = cover_url if cover_url is not None else source.get("cover_url")
        if covers is None:
            covers = source.get("cover_items") or []
        if isinstance(covers, (str, dict)):
            covers = [covers]
        cover_objects = [_url_object(value) for value in (covers or [])]
        items = cover_items if cover_items is not None else source.get("cover_items") or []
        payload = {
            "uid": uid,
            "secUID": sec_uid,
            "name": name,
            "avatar": _url_object(avatar),
            "cover_items": list(items or []),
            "cover_url": cover_objects,
        }
        # Keep any existing card-specific fields (for example follow state)
        # and then apply explicit overrides.
        payload.update({k: v for k, v in source.items() if k not in payload})
        payload.update(extra)
        return payload

    @staticmethod
    def sticker_content(*, image_id, uri, display_name="", image_type="gif",
                        width=0, height=0, package_id=0, url_list=None,
                        **extra) -> dict:
        """Build the BIG_EMOJI payload expected by PC IM.

        Sticker CDN metadata is intentionally caller supplied: arbitrary local
        images are not automatically converted into a platform sticker.
        """
        payload = {
            "aweType": 510,
            "display_name": display_name,
            "height": int(height or 0),
            "width": int(width or 0),
            "image_id": str(image_id),
            "image_type": image_type,
            "package_id": int(package_id or 0),
            "show_notice": False,
            "resource_type": 0,
            "updateConversationTime": True,
            "createdAt": 0,
            "is_card": False,
            "msgHint": "",
            "url": {
                "height": int(height or 0),
                "width": int(width or 0),
                "uri": uri,
                "url_list": list(url_list or ([uri] if uri else [])),
            },
        }
        payload.update(extra)
        return payload


DouyinIMMediaUploader = DouyinIMMedia

__all__ = ["DouyinIMMedia", "DouyinIMMediaUploader"]
