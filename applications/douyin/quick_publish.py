#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音快速发布：扫码/手机号登录后立即发布图集或视频。

使用方式和 ``../KuaiShou-Spider/quick_publish.py`` 一样：只修改下面的“用户配置”，
然后直接运行 ``python quick_publish.py``。脚本不读取 Chrome，不保存扫码/手机号
登录凭证；登录态只存在于本次 Python 进程中。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

from dotenv import load_dotenv

from builder.auth import DouyinAuth
from dy_apis.douyin_creator_api import DouyinCreatorAPI


# ============================== 用户配置 ============================== #
# 修改这里，然后运行：python quick_publish.py
LOGIN_MODE = "phone"                 # "qr"、"phone" 或 "cookie"
MEDIA_TYPE = "image"              # "image" 或 "video"

# 图集：至少一张图片，可填多张；支持 jpg/png/webp 等底层 API 可识别的格式。
IMAGE_PATHS = [
    r"C:\Users\Administrator\Desktop\qrcode_1787380640927.jpg",
]
# 视频：MEDIA_TYPE="video" 时使用这一项。
VIDEO_PATH = r"D:\media\demo.mp4"

TITLE = ""                        # 标题，建议不超过 20 个字符
DESC = ""                         # 正文描述
VISIBILITY = 1                     # 0 公开、1 仅自己可见、2 好友可见
ALLOW_DOWNLOAD = True              # 是否允许其他用户保存下载

PHONE = ""                        # 手机号登录；留空时运行中输入
SMS_CODE = ""                     # 留空时运行中交互式输入，不写入文件
COOKIES = ""                      # cookie 模式可填；扫码/手机号模式不需要
QR_TIMEOUT = 300.0                # 扫码最长等待秒数
SHOW_QR = True                     # 是否在终端绘制二维码


def _validate_config() -> Tuple[str, List[str]]:
    if LOGIN_MODE not in {"qr", "phone", "cookie"}:
        raise ValueError("LOGIN_MODE 必须是 qr、phone 或 cookie")
    if MEDIA_TYPE not in {"image", "video"}:
        raise ValueError("MEDIA_TYPE 必须是 image 或 video")
    if VISIBILITY not in {0, 1, 2}:
        raise ValueError("VISIBILITY 必须是 0、1 或 2")
    if len(str(TITLE or "")) > 20:
        raise ValueError(f"TITLE 不能超过 20 个字符，当前为 {len(TITLE)} 个")

    if MEDIA_TYPE == "image":
        paths = [str(Path(value).expanduser().resolve()) for value in IMAGE_PATHS]
        if not paths:
            raise ValueError("IMAGE_PATHS 至少需要一张图片")
    else:
        paths = [str(Path(VIDEO_PATH).expanduser().resolve())]

    for path in paths:
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"媒体文件不存在：{file_path}")
        if file_path.stat().st_size <= 0:
            raise ValueError(f"媒体文件为空文件：{file_path}")
    return MEDIA_TYPE, paths


def _response_error(response: Any) -> Optional[str]:
    """返回手机号登录失败原因；不把一次性 mobile_ticket 打到终端。"""
    if not isinstance(response, dict):
        return f"服务端返回了非 JSON 对象：{response!r}"
    data = response.get("data")
    nested = data if isinstance(data, dict) else {}
    error_code = response.get("error_code", nested.get("error_code"))
    message = str(response.get("message") or "").lower()
    if error_code not in (None, 0, "0") or message in {"error", "fail", "failed"}:
        description = (
            response.get("description")
            or nested.get("description")
            or response.get("message")
            or "未知错误"
        )
        return f"error_code={error_code!r}，{description}"
    return None


def _response_summary(response: Any) -> str:
    if not isinstance(response, dict):
        return repr(response)
    data = response.get("data")
    nested = data if isinstance(data, dict) else {}
    summary = {
        key: value
        for key, value in (
            ("message", response.get("message")),
            ("error_code", response.get("error_code", nested.get("error_code"))),
            ("description", response.get("description", nested.get("description"))),
        )
        if value not in (None, "")
    }
    return json.dumps(summary, ensure_ascii=False)


def login() -> DouyinAuth:
    """执行一次登录并返回可直接发布的 creator Auth。"""
    if LOGIN_MODE == "cookie":
        cookie = (COOKIES or os.environ.get("DY_COOKIES") or "").strip()
        if not cookie:
            raise RuntimeError(
                "COOKIE 模式需要填写 COOKIES，或设置环境变量 DY_COOKIES；"
                "扫码/手机号模式不需要它"
            )
        print("正在用本地 Cookie 初始化抖音创作者会话……", flush=True)
        return DouyinAuth.from_cookie(cookie, bootstrap_creator=True)

    if LOGIN_MODE == "qr":
        print("正在申请抖音二维码，请用抖音 App 扫描并确认……", flush=True)

        def on_qrcode(url: str) -> None:
            print(f"二维码链接：{url}", flush=True)

        return DouyinAuth.from_qrcode_login(
            timeout=QR_TIMEOUT,
            show_qr=SHOW_QR,
            on_qrcode=on_qrcode,
            bootstrap_creator=True,
        )

    phone = (PHONE or os.environ.get("DY_PHONE") or "").strip()
    if not phone:
        phone = input("手机号：").strip()
    if not phone:
        raise ValueError("手机号不能为空")

    print("正在申请短信验证码……", flush=True)
    # 关键点：send_code 与 sms_login 必须复用同一个 auth，不能重新创建。
    pending_auth, response = DouyinAuth.start_phone_login(phone)
    error = _response_error(response)
    if error:
        raise RuntimeError(
            f"短信验证码申请失败：{error}\n响应摘要：{_response_summary(response)}"
        )

    code = (SMS_CODE or os.environ.get("DY_SMS_CODE") or "").strip()
    if not code:
        # Use a normal visible prompt.  Windows terminals can make
        # getpass.getpass() look like the process has hung because its prompt
        # is not always rendered by redirected/embedded consoles.
        print("短信验证码已申请，请查看手机短信。", flush=True)
        code = input("请输入短信验证码（6位）：").strip()
    if not code:
        raise ValueError("短信验证码不能为空")

    print("正在提交短信验证码……", flush=True)
    return DouyinAuth.from_phone_login(
        phone, code, auth=pending_auth, bootstrap_creator=True,
    )


def publish(auth: DouyinAuth, media_type: str, media_paths: Sequence[str]):
    """调用现有发布 API；不传 timing，代表登录后立即提交。"""
    kwargs = dict(
        title=TITLE,
        desc=DESC,
        visibility=VISIBILITY,
        allow_download=ALLOW_DOWNLOAD,
        timing=None,
    )
    if media_type == "image":
        print(f"登录成功，开始上传并发布图集（{len(media_paths)} 张）……", flush=True)
        return DouyinCreatorAPI.post_images(
            auth, images=list(media_paths), **kwargs,
        )

    print(f"登录成功，开始上传并发布视频：{media_paths[0]}", flush=True)
    return DouyinCreatorAPI.post_video(
        auth, video=media_paths[0], **kwargs,
    )


def main() -> int:
    load_dotenv()
    auth: Optional[DouyinAuth] = None
    try:
        media_type, media_paths = _validate_config()
        auth = login()
        ok, message, result = publish(auth, media_type, media_paths)
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI 入口统一报告错误
        print(f"快速发布失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if auth is not None:
            try:
                auth.close()
            except Exception:
                pass

    if ok:
        print(f"发布成功：{message}", flush=True)
        if result:
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0

    print(f"发布失败：{message}", file=sys.stderr)
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
