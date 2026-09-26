# -*- coding: utf-8 -*-
"""执行 challenge 下发的 template，产出 `p_in` / `e_in`。

**这是 2026-08-22 挖到的关键一环**：`/passport/web/challenge/` 的响应不只是
「成功」，`data.template` 是一段**服务端每次现下发的 77KB JS**（UMD 模块
`__p_ch`），浏览器要执行它，产出：

- `p_in`：设备指纹的 sha256，正是 Cookie `gulu_source_res` 里那个字段
- `e_in`：反自动化探针结果，正是 Cookie `sdk_source_info` 的明文

我们之前两个都是**伪造**的（`p_in` 用随机 hex、`e_in` 靠猜键名还猜错了 3 个），
等于 challenge 走完了却没交作业。

模块采集 38 项（fonts / canvas / webgl / screen / navigator / matchMedia /
math ...，FingerprintJS 那一套），所以必须补环境跑。执行器是
`utils/challenge_template_runner.js`，用 Node 的 `vm` 跑，设备值全部取自
`utils/fingerprint.py` 的档案 —— 与 query / cookie / dtrait 几处指纹自洽。

> 为什么不纯算：38 项里 canvas / webgl / fonts 要真实渲染，
> 而且 template 是**服务端现下发**的，每次内容可能变，硬编码没有意义。
> 让 JS 自己跑一遍最稳。
"""

import json
import os
import subprocess
import tempfile

from utils.fingerprint import get_profile

_HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(_HERE, "challenge_template_runner.js")


def _device_profile():
    """给 runner 的设备档案，字段与 fingerprint 档案同源。"""
    p = get_profile()
    g = p["geo"]      # (w, innerH, w, outerH, w, availH, w, h)
    return {
        "ua": p["ua"],
        "browser_major": int(str(p["browser_version"]).split(".")[0]),
        "cpu_core_num": int(p["cpu_core_num"]),
        "device_memory": int(p["device_memory"]),
        "screen_width": int(p["screen_width"]),
        "screen_height": int(p["screen_height"]),
        "avail_width": g[4],
        "avail_height": g[5],
        "inner_width": g[0],
        "inner_height": g[1],
        "outer_width": g[2],
        "outer_height": g[3],
        "webgl_vendor": p["webgl_vendor"],
        "webgl_renderer": p["webgl_renderer"],
        "languages": ["zh-CN", "zh", "en", "zh-TW", "ja"],
    }


def run_template(template_js: str, timeout: int = 60):
    """跑 template，返回 `{"p_in": ..., "e_in": {...}}`；失败返回 None。"""
    tmpdir = tempfile.mkdtemp(prefix="dych_")
    tpl = os.path.join(tmpdir, "t.js")
    prof = os.path.join(tmpdir, "p.json")
    try:
        with open(tpl, "w", encoding="utf-8") as f:
            f.write(template_js)
        with open(prof, "w", encoding="utf-8") as f:
            json.dump(_device_profile(), f, ensure_ascii=False)
        out = subprocess.run(
            ["node", RUNNER, tpl, prof],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace")
        if out.returncode != 0:
            return None
        data = json.loads((out.stdout or "").strip().splitlines()[-1])
        return data.get("result") if data.get("ok") else None
    except Exception:
        return None
    finally:
        for p in (tpl, prof):
            try:
                os.unlink(p)
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
