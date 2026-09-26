# -*- coding: utf-8 -*-
"""Pure challenge body generation recovered from the passport web SDK.

The browser pipeline is deterministic for one device/browser bundle:

1. ``collectFingerprintInfo()`` returns the ordered 36-field device object.
2. SHA-256(userAgent) is the AES-256 key; its last 16 bytes are the CBC IV.
3. The plaintext is ``btoa(JSON.stringify(fingerprint))``.
4. AES-CBC/PKCS#7 ciphertext is serialized with Base64URL (including padding).
5. ``sk`` is ``encodeURIComponent(stack)`` encoded as UTF-8, XOR 5, then hex.

Canvas/font/WebGL values are device artifacts stored in the local fingerprint
profile. The crypto itself is fully calculated at runtime.
"""

import base64
import hashlib
import json
import os
import urllib.parse

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from utils.fingerprint import get_profile


_PROFILE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "challenge_profile.json")
_profile_cache = None


def _load_profile():
    global _profile_cache
    if _profile_cache is None:
        with open(_PROFILE_PATH, encoding="utf-8") as handle:
            _profile_cache = json.load(handle)
    return _profile_cache


def _js_json(value):
    """Match JSON.stringify for this ASCII-only fingerprint object."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    try:
        return text.encode("latin-1")
    except UnicodeEncodeError as err:
        raise ValueError("challenge fingerprint contains non-btoa characters") from err


def build_challenge_sign(fingerprint=None, ua=None):
    profile = _load_profile()
    fingerprint = profile["fingerprint"] if fingerprint is None else fingerprint
    ua = ua or get_profile()["ua"]
    key = hashlib.sha256(ua.encode("utf-8")).digest()
    plaintext = base64.b64encode(_js_json(fingerprint))
    padding = 16 - len(plaintext) % 16
    padded = plaintext + bytes([padding]) * padding
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[16:])).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.urlsafe_b64encode(ciphertext).decode("ascii")


def build_bit_env(passportiv, ua=None):
    """Derive the ``bit_env`` cookie from challenge ``passportiv``.

    The passport page does not invent a 512-character cookie locally.  Its
    challenge response contains ``data.passportiv`` (currently a 380-byte
    Base64URL string); the page encrypts that exact string with the same
    CryptoJS AES-256-CBC profile used for ``sign`` and stores the resulting
    Base64URL ciphertext as ``bit_env``.  This is therefore a pure local
    calculation once the challenge response has been obtained over HTTP.

    ``passportiv`` is deliberately required: falling back to a random value
    would produce a shape-correct but unverifiable cookie and is forbidden in
    strict browser-alignment mode.
    """
    if passportiv is None or passportiv == "":
        raise ValueError("challenge passportiv is required to derive bit_env")
    if not isinstance(passportiv, str):
        passportiv = str(passportiv)
    ua = ua or get_profile()["ua"]
    key = hashlib.sha256(ua.encode("utf-8")).digest()
    plaintext = passportiv.encode("utf-8")
    padding = 16 - len(plaintext) % 16
    padded = plaintext + bytes([padding]) * padding
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[16:])).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.urlsafe_b64encode(ciphertext).decode("ascii")


def build_challenge_sk(stack=None):
    if stack is None:
        profile = _load_profile()
        # The challenge stack contains the concrete bundle URLs and therefore
        # is part of the browser wire value, not a cosmetic diagnostic field.
        # Keep the older fixture for legacy replay, but use the 2026-08-29
        # Chrome 151 stack for the current passport profile.  An explicit
        # capture override is provided for later bundle rollouts.
        stack = os.getenv("DY_PASSPORT_FIXED_CHALLENGE_STACK")
        if not stack:
            cookie_profile = (os.getenv("DY_PASSPORT_COOKIE_PROFILE")
                              or "chrome_current").strip().lower()
            if cookie_profile == "chrome_current":
                stack = profile.get("stack_current") or profile["stack"]
            else:
                stack = profile["stack"]
    encoded = urllib.parse.quote(stack, safe="-_.!~*'()")
    return "".join(format(byte ^ 5, "x") for byte in encoded.encode("utf-8"))


def build_challenge_body(fingerprint=None, stack=None, ua=None):
    return urllib.parse.urlencode([
        ("sign", build_challenge_sign(fingerprint=fingerprint, ua=ua)),
        ("sk", build_challenge_sk(stack=stack)),
    ])
