# -*- coding: utf-8 -*-
"""Run the page acrawler bundle without starting a browser.

The bundled VMP is executed in a small Node ``vm`` context whose DOM and
navigator prototypes are shaped from the captured Chromium page.  This is
intentionally separate from the HTTP client: callers provide the exact
nonce/cookie/header inputs and receive the generated ``__ac_signature`` plus
the cookie serialization observed by the runner.

This helper is an implementation of the page JavaScript, not a random or
length-only placeholder.  The default context is the variant that has been
verified byte-for-byte against the checked-in historical browser fixture.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Mapping, Optional, Union


_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME = _ROOT / "utils" / "acrawler_runtime"
_RUNNER = _RUNTIME / "run_ac_node.js"
_DEFAULT_VARIANT = "chrome-doc-native-proto"


def _cookie_header(cookie: Union[str, Mapping[str, object], None]) -> str:
    if cookie is None:
        return ""
    if isinstance(cookie, str):
        return cookie
    return "; ".join(f"{name}={value}" for name, value in cookie.items())


def _cookie_map(header: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for token in str(header or "").split(";"):
        token = token.strip()
        if not token or "=" not in token:
            continue
        name, value = token.split("=", 1)
        out[name.strip()] = value
    return out


def generate_ac_signature(
    nonce: str,
    cookie: Union[str, Mapping[str, object], None] = None,
    *,
    url: str = "https://www.douyin.com/jingxuan",
    referrer: str = "",
    ua: Optional[str] = None,
    variant: Optional[str] = None,
    vm_file: Optional[Union[str, os.PathLike[str]]] = None,
    now_ms: Optional[Union[int, str]] = None,
    strict: bool = True,
) -> dict:
    """Generate an acrawler signature from explicit page inputs.

    Returns a JSON-safe dictionary containing ``sig``, ``cookie_header``,
    ``cookie`` and ``provenance``.  On a strict failure no fallback signature
    is returned; this is important because an empty/random signature changes
    the security decision while looking superficially well-formed.
    """
    nonce = str(nonce or "")
    if not nonce:
        if strict:
            raise ValueError("acrawler nonce is required")
        return {"sig": "", "cookie_header": _cookie_header(cookie),
                "cookie": _cookie_map(_cookie_header(cookie)),
                "provenance": "unproven_synthetic"}
    if not _RUNNER.is_file():
        if strict:
            raise FileNotFoundError(f"acrawler runner not found: {_RUNNER}")
        return {"sig": "", "cookie_header": _cookie_header(cookie),
                "cookie": _cookie_map(_cookie_header(cookie)),
                "provenance": "unproven_synthetic"}

    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        if strict:
            raise RuntimeError("Node.js is required for page acrawler execution")
        return {"sig": "", "cookie_header": _cookie_header(cookie),
                "cookie": _cookie_map(_cookie_header(cookie)),
                "provenance": "unproven_synthetic"}

    header = _cookie_header(cookie)
    env = os.environ.copy()
    env.update({
        "AC_NONCE": nonce,
        "AC_SIGN_NONCE": nonce,
        "AC_COOKIE_ONLY": header,
        "AC_HREF": str(url or "https://www.douyin.com/jingxuan"),
        "AC_REFERRER": str(referrer or ""),
        "AC_VARIANT": str(variant or os.getenv("DY_AC_VARIANT") or _DEFAULT_VARIANT),
    })
    if ua:
        env["AC_UA"] = str(ua)
    # The standalone runner is intentionally deterministic when a clock is
    # supplied.  Supplying the current epoch explicitly also avoids Node's
    # host Date leaking through the vm realm (the VMP's constructor probes
    # otherwise take a different branch and can abort before returning JSON).
    env["AC_NOW"] = str(now_ms if now_ms not in (None, "")
                        else int(time.time() * 1000))
    if vm_file:
        env["AC_VM_FILE"] = str(vm_file)
    # The checked-in Chromium capture supplies the exact 2D canvas data URL
    # used by the parity fixture.  A deployment on another machine may set
    # DY_AC_CANVAS_DATA_URL after collecting its own page probe.
    canvas = os.getenv("DY_AC_CANVAS_DATA_URL")
    if canvas:
        env["AC_CANVAS_DATA_URL"] = canvas
    else:
        canvas_file = _RUNTIME / "canvas_actual_exact.json"
        if canvas_file.is_file():
            try:
                env["AC_CANVAS_DATA_URL"] = json.loads(
                    canvas_file.read_text(encoding="utf-8")
                )
            except Exception:
                pass

    try:
        completed = subprocess.run(
            [node, str(_RUNNER)],
            cwd=str(_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except Exception as exc:
        if strict:
            raise RuntimeError("acrawler Node runner could not be started") from exc
        return {"sig": "", "cookie_header": header,
                "cookie": _cookie_map(header),
                "provenance": "unproven_synthetic"}

    payload = None
    for line in reversed((completed.stdout or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    sig = str((payload or {}).get("sig") or "")
    if completed.returncode != 0 or not sig.startswith("_"):
        if strict:
            # Do not include stdout/stderr: they can contain page cookies or
            # other sensitive challenge material.
            raise RuntimeError("acrawler Node runner failed to produce a signature")
        return {"sig": "", "cookie_header": header,
                "cookie": _cookie_map(header),
                "provenance": "unproven_synthetic"}

    out_header = str((payload or {}).get("cookie") or header)
    before = _cookie_map(header)
    after = _cookie_map(out_header)
    mutations = {
        name: value for name, value in after.items()
        if before.get(name) != value
    }
    return {
        "sig": sig,
        "cookie_header": out_header,
        "cookie": after,
        "cookie_mutations": mutations,
        "provenance": "node_page_js",
        "variant": env["AC_VARIANT"],
    }


__all__ = ["generate_ac_signature"]
