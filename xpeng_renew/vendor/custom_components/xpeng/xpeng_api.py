"""Headless XPENG API client for MQTT credential management.

Extracted from Feylinchen's xpgui.pyw, mqtt_generator.pyw, and
xp_authorization.py — all GUI/tkinter dependencies removed.

Provides:
- XPENG API request signing (HMAC-SHA256 + AES-GCM authorization blobs)
- Tenant/key pool management (teeIndiv4OverseaApp)
- Login flow (email verification + password login)
- MQTT credential generation (createMqttAccount)
"""
from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import logging
import os
import re
import secrets
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from typing import Any

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding as sym_padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def _default_cert_dir() -> str:
    """Where the mTLS certificates live when no directory was configured.

    Three layouts load this module and they disagree about the answer:

    * Home Assistant imports it as a package, so const.py answers with the
      config directory ("/config/custom_components/xpeng/certs").
    * A development checkout or the tools/ directory loads it as a top-level
      module, where const.py is usually not importable at all and the certs sit
      beside this file.
    * The renewal add-on vendors this file and deliberately ships no certs of
      its own: it reads the integration's from /config through its config:rw
      map.

    Check every candidate against the filesystem and take the first that
    actually holds the CA chain, rather than trusting one hardcoded path.

    Loading by path used to skip that check, which cost every unattended
    renewal: the add-on resolved the cert directory next to its own vendored
    copy, found no certs there, and died at "mTLS certificate file(s) missing"
    before it could even open a socket (2026-10-10). Home Assistant never
    reached that branch, so only the add-on was broken — and silently, because
    the traceback went to a stream the worker did not report.
    """
    candidates: list[str] = []
    try:  # normal package import
        from .const import DEFAULT_CERT_DIR as configured
    except ImportError:  # loaded as a top-level module
        try:
            from const import DEFAULT_CERT_DIR as configured
        except ImportError:
            configured = ""
    if configured:
        candidates.append(configured)
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs"))
    # A copy that carries no certs of its own (the renewal add-on) reads the
    # installed integration's, which is where Home Assistant keeps them.
    candidates.append("/config/custom_components/xpeng/certs")
    # The update-safe location the README recommends, outside the integration
    # directory: a HACS update reinstalls custom_components/xpeng/, so keys kept
    # inside it can go with it. Tried LAST, so an install already resolving to the
    # directory above keeps resolving to it - this only ever adds somewhere to
    # look, which is what lets the recommended layout need no option set.
    candidates.append("/config/xpeng-certs")
    for candidate in candidates:
        if candidate and os.path.isfile(os.path.join(candidate, "ca-chain.crt")):
            return candidate
    # Nothing is installed yet. Name the configured location so the error the
    # caller raises points at the directory that has to be populated.
    return candidates[0] if candidates else ""


def resolve_cert_dir(configured: str = "") -> str:
    """The certificate directory to actually read from.

    ``configured`` is what an entry stored, and it wins - but only while it still holds
    the files. A stored path can be wrong for a reason nobody typed: the config flow
    records a default when a client submits no certificate directory, which is what the
    renewal app does when it bootstraps a fresh install, and that recorded default
    outlives the directory it names.

    Measured 2026-10-11 on a fresh install whose certificates were in
    `/config/xpeng-certs`: the entry recorded `/config/custom_components/xpeng/certs`,
    which holds only a README, and setup died inside the TLS context with no entities
    and nothing pointing at the cause. Falling back to the filesystem search turns that
    into a warning and a working install.
    """
    # Imported here, and defensively, for the same reason the resolver below is: this
    # file is loaded three ways (as a package, as a top-level module, and vendored into
    # the renewal add-on) and only the first can rely on relative imports succeeding.
    try:  # normal package import
        from .mqtt_client import CERT_FILES
    except ImportError:
        try:
            from mqtt_client import CERT_FILES  # type: ignore
        except ImportError:  # pragma: no cover - last resort
            CERT_FILES = ("ca-chain.crt", "client-cert-0.pem", "client-key.pem")  # type: ignore
    if configured and all(
        os.path.isfile(os.path.join(configured, name)) for name in CERT_FILES
    ):
        return configured
    return _default_cert_dir()


DEFAULT_CERT_DIR: str = _default_cert_dir()

try:  # normal package import
    from .xpeng_errors import LoginExpiredError, is_token_expired_response
except ImportError:  # loaded as a top-level module (tests/ and tools/ do this)
    try:
        from xpeng_errors import LoginExpiredError, is_token_expired_response
    except ImportError:  # pragma: no cover - last-resort shim
        class LoginExpiredError(Exception):  # type: ignore[no-redef]
            def __init__(self, code: int = 0, message: str = "") -> None:
                super().__init__(f"login expired (code {code})")
                self.code = code
                self.message = message

        def is_token_expired_response(data: object) -> bool:  # type: ignore[misc]
            # Kept in step with xpeng_errors.TOKEN_EXPIRED_CODES by hand: this
            # shim only exists for callers that cannot import that module at
            # all (tools/ and tests/ loading this file by path). It lagged once
            # and cost a probe its ability to see 12010202, the answer
            # /sso/app/v3/token/refresh gives for a retired session.
            return isinstance(data, dict) and data.get("code") in (
                12010101, 12010104, 12010202,
            )

_LOGGER = logging.getLogger(__name__)

# ============================================================
# Embedded constants
# ============================================================

APP_SECRET = "d0j2mgd15qqoblq2"

PROD_RSA_PUBLIC_KEY_B64 = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC/prgqbRz8BxqtvxXv/snWv663"
    "gDw25wLSsmFoQIGnopy7Pd2rTJLFVSyu84hy3OvYbjrDs/uvTAt/LwHREJnsNX+X"
    "VM504/9aYTa+Te4RrBv7qIyx/tnWy/InhE1d+9yKRSHSvqvFXBmWu6J/jqDOQutz"
    "Y9Bmit2sg9KF8x31HwIDAQAB"
)

NETHT_RSA_PUBLIC_KEY_DER_B64 = (
    "MIIBCgKCAQEA1Wm7IVWJ8pRJDHHO+UdA2VBDYEtcLaR120vtZd9SN01iZLqFGioh"
    "Lms6jCGQc973DFZZ7t98IvS7Ufvh1NDHsGpbJqMIosxKUMzWMqGKFdN03+aZSS7RTFR8"
    "BFwD9NWyh7YtjnfaCtNT46dCklCI8B7GFcbKhH9GtWNAyCECd8NODRyseTC6nKh4p/BG"
    "T7vFLxqYU8FUN19D2xSIkscadhRhIFm+Eio3yKggCG5Y/zYBXSz+6JvNm7zfedFOw77N"
    "CL0cR1WohGLOztm8tjJKJjhzta5xl/Vz8TB89cVzoFvvYhWHSh/FAjP3Ujh/bHWwGKz"
    "9p7+8xMSe6cbAKB4lxQIDAQAB"
)

# NetEase HTProtect anti-bot payload template
EMBEDDED_PTOKEN_TEMPLATE_B64 = "ENSAltYMSgBwAoIBAJIBDAoGc3RhdHVzEgIyMJIBDAoFZHRpbWUSAzE0MpIBDgoFY3RpbWUSBTEwNTM4kgEJCgJ0chIDMjAzkgEHCgJsbhIBM5IBCgoCd24SBDMsMyyaAVsIuBcSDGh0cF90ZXh0X21kNRogNWVkMDI3YmJlOTg1MjVlZWUzMDQ4NGU1ZjE1OGU5ZDAiIDVlZDAyN2JiZTk4NTI1ZWVlMzA0ODRlNWYxNThlOWQwKO/z+M8GmgEtCAoSAmZwGgExIhwvc3lzdGVtL2FkZG9uLmQvOTktbWFnaXNrLnNoKIT0+M8GogEtL2RhdGEvdXNlci8wL2NvbS54aWFvcGVuZy5nbG9iYWxjYXJpbmZvL2ZpbGVzsAEguAECwgEWYW5kcm9pZC5vcy5CaW5kZXJQcm94ecoBFmFuZHJvaWQub3MuQmluZGVyUHJveHnSASBkZGFkODJlMGQ5Yzg1MGFlNmE5ZDc2MjY0ZGY5ODgyONoBIGRkYWQ4MmUwZDljODUwYWU2YTlkNzYyNjRkZjk4ODI44gEkNTJkY2FhNTYtMTkxOS00OTczLWJjMDItMmMyM2FiMTViMzUy6AHj8/jPBvAB10+KAglub3RfZm91bmSQAgiYAgGyAgMyMDPQAgTiAiA4MjkxNmIzMjNlMzgwMzc5NWFmMzdhOWU3ZTNlYzYyOOoCEFlEMDA5MDczMTE1ODI4MDbyAhNpbml0OjEtZ3Q6Mi1pb2NfODoy+gIRCgQvZGV2EPic6f///////wH6AhIKBS9kYXRhEPr//////////wGCAwgKBGluaXQQAYIDBgoCZ3QQAoIDCQoFaW9jXzgQAogD5weQAwGYA470+M8GsAPA8bQC0gNhL2RhdGEvYXBwL35+QXNpTy1Vd1ZRR1BGRVRXNlpUazk5dz09L2NvbS54aWFvcGVuZy5nbG9iYWxjYXJpbmZvLWlfRzk2ZFpxNXJPdEl2aVB2YlQyOGc9PS9iYXNlLmFwa9oDIGRkYWQ4MmUwZDljODUwYWU2YTlkNzYyNjRkZjk4ODI46gMlMmEwMDoxZTo1ZGQxOmFlMDE6OTQzMTo0ZGZmOmZlOGU6NWY2MfIDJTJhMDA6MWU6NWRkMTphZTAxOmJmNjA6ZDI3Yjo5OGUzOmM1ZmKCBCFkZGFkODJlMGQ5Yzg1MGFlNmE5ZDc2MjY0ZGY5ODgyODuIBASQBCvKBAQyMDAxEoALEAEiBTUuNi44MgIxNjoFMi45LjBAJEoaY29tLnhpYW9wZW5nLmdsb2JhbGNhcmluZm9SBjMuMTEuMFji1d2owGdg4tXdqMBnaiBjOTgwOTY1Y2E5NTkxMTc1MDY0OTJkY2E5M2UzZjQ3MXoaY29tLnhpYW9wZW5nLmdsb2JhbGNhcmluZm+CAQUyNjIwMogBApIBB3NhbXN1bmeaAQhTTS1HOTc1RqIBDUc5NzVGWFhTR0hXQzKIAgiyAhA0NTE0NGI4ODE0MzRhNTBlwAICyAKQxy3QAtzjHPICCjIwMjYtMDMtMDH6AgoxMTY0Y2EwMDVmiAMEkoiiLqh4AAC0djhhLGFybWVhYmktdjdhLGFybWVhYmmaAwkxNDQweDMwNDCgA3K6AxBTYW1zdW5nIFJJTCB2NS4wwgMNTE9BREVELEFCU0VOVMoDR2xpbmVhZ2VfYmV5b25kMmx0ZS11c2VyZGVidWcgMTYgQlA0QS4yNTEyMDUuMDA2IDExNjRjYTAwNWYgcmVsZWFzZS1rZXlz0gNac2Ftc3VuZy9saW5lYWdlX2JleW9uZDJsdGUvYmV5b25kMmx0ZToxNi9CUDRBLjI1MTIwNS4wMDYvMTE2NGNhMDA1Zjp1c2VyZGVidWcvcmVsZWFzZS1rZXlz2gMKYmV5b25kMmx0ZeoDCmV4eW5vczk4MjDyAxJsaW5lYWdlX2JleW9uZDJsdGX6AwpleHlub3M5ODIwggQNdW5pdmVyc2FsOTgyMIoEB3NhbXN1bmeSBApiZXlvbmQybHRl8gRCZmJlNWZkMDVkYmFlMjVmZDk3YmI5ZDQ3ZmEwMWNiMDU5YzUzZGY3ZTZiNzEyZjQwMzlkNzFmNzZmNGU3NmQ5OC1B+ASAwJDEGoAFgMD+/jmIBZSluTOQBYCwvDeaBSAwYTQ1YTgxZTcyZGMwMjA3YWI0NTY3YmM2ZWRkZDQ0N6AFiECoBcgBsAUK0AUB2AUBmAYBoAYBqAYB0gYDOHww6gYVMTkyLjE2OC4xNzguMTE4OjE2NTU0yAf+lMvI4DPYBxDiByQ3YjQ3MThjMC0wMzU1LTQyMmEtOWJkNS00NzlkNjhmZjdjMTOKCCQyOGNlMmU0Ni0zYzljLTQ3NzctYjAxOS02ZWFmY2VmOThiYzWQCEaoCAG6CBA1MWU0YzdhN2UzYzYzY2E58AgBmAkUqgkFWFBFTke4CdQWwgkaY29tLnhpYW9wZW5nLmdsb2JhbGNhcmluZm/YCQH6CQ9CUDRBLjI1MTIwNS4wMDaCCgR1c2VyigoMcmVsZWFzZS1rZXlzqgoHdW5rbm93brIKLjE3NzU2ODEyNzM1MTUsMTc3NTY4MTI3MzUxNSwxNzc1NjgxMjczNTE1LDUwNzG6Ci4xNzc1NjgxMjYzMjQzLDE3NzYxOTM3MTQ4MjksMTc3NjE5MzcxNDgyOSw0MzM14ArX+eb21jPoCsD65PbWM/IKMWFmOTAzMWY2LTc2ODQtNGMyNS1hMjQ2LWRlNGU2NGMxMmYxMi01NGM0OTY2NmYyYWT4CrrQoq2+Z4gLAeALAuoLDTE5Mi4xNjguMTc4LjH4C5ClmIqrZ4IMHFNhdCBBcHIgIDQgMTM6MTY6MTMgVVRDIDIwMjaaDAllbmNyeXB0ZWTgDKScAbINJGE2YjE4Mzc1LTM4OGItNDA5Mi04MzdiLTg2NjIzZTBhZDdhN+INAmRl6A2eBJgOAaAOykC4DsCpB9gOHOAOAvIOBWVuLVVT+g4FZGUtREWCDwNkZSyKDwVkZS1ERZIPAkRF2A/5gwHgD66Dyu2tZ+gPsoCejsFn8A+ygJ6OwWeQENdPmBDXT+gQPLARAcoRBTI2MjAy0hECZGXaEQJkZQ=="

CLIENT_KEY_PASSWORD = "xiaopengapp"

_NONCE_COUNTER = [0]

DEFAULT_APP_CONFIG: dict[str, str] = {
    "AppId": "xp_app_norway_android",
    "Brand": "XPENG",
    "ClientType": "1",
    "ClientEncoding": "None",
    "EncryptionType": "2",
    "TeeVersion": "2",
    "LoginSmsType": "88",
    "LoginPTokenType": "1",
    "LoginTenantVersion": "2",
    "UseMTLS": "1",
    "LoginBaseUrl": "https://apps-global.xiaopeng.com",
    "MqttPath": "/biz/v5/mqtt/createMqttAccount/app",
    "Client": (
        "ve=3.11.0;br=samsung;mo=SM-G975F;st=1;sv=16;"
        "no=null;pn=com.xiaopeng.globalcarinfo;cn=gp;re=;ma=;"
        "ro=Other;me=0.00G;cp=arm64-v8a;cr=0;nt=WIFI;"
        "t=0;gp=;ln=de;ulc=de;rg=DE;tz=Europe/Berlin"
    ),
    "ExtraHeaders": (
        "Country: DE\n"
        "Language: de-DE\n"
        "Client-Type: APP\n"
        "Mockfilter: 1\n"
        "Accept: application/json\n"
        "Content-Type: application/json; charset=utf-8\n"
        "User-Agent: com.xiaopeng.globalcarinfo/3.11.0 "
        "(8801; SM-G975F; Android; 16; samsung; androidstore)"
    ),
}


# ============================================================
# Encoding / hashing helpers
# ============================================================

def md5_hex(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def hmac_hex(secret: str, msg: str) -> str:
    return _hmac.new(
        secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def b64decode_padded(value: str) -> bytes:
    value = "".join(value.strip().split()).replace("-", "+").replace("_", "/")
    value += "=" * ((4 - len(value) % 4) % 4)
    return base64.b64decode(value)


def b64_bytes(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def compact_json(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def compact_gson_json(obj: dict) -> str:
    """Mimic Android Gson HTML-safe escaping."""
    text = compact_json(obj)
    return (
        text.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("=", "\\u003d")
        .replace("'", "\\u0027")
    )


# ============================================================
# Nonce / Xp-Client helpers
# ============================================================

def gen_nonce_and_ts() -> tuple[str, int]:
    ts_ms = int(time.time() * 1000)
    _NONCE_COUNTER[0] = (_NONCE_COUNTER[0] + 1) % 10000
    nonce = f"{ts_ms}{_NONCE_COUNTER[0]:04d}"
    return nonce, ts_ms


def patch_client_t(client: str, ts_ms: int) -> str:
    parts = client.split(";")
    found = False
    for i, part in enumerate(parts):
        if re.fullmatch(r"t=\d+", part.strip()):
            parts[i] = f"t={ts_ms}"
            found = True
            break
    if not found:
        parts.append(f"t={ts_ms}")
    return ";".join(parts)


def patch_client_segment(client: str, key: str, value: str) -> str:
    parts = client.split(";") if client else []
    prefix = key + "="
    for i, part in enumerate(parts):
        if part.strip().startswith(prefix):
            parts[i] = prefix + value
            return ";".join(parts)
    parts.append(prefix + value)
    return ";".join(parts)


def extract_client_segment(client: str, key: str) -> str:
    prefix = key + "="
    for part in client.split(";"):
        part = part.strip()
        if part.startswith(prefix):
            return part[len(prefix):]
    return ""


# ============================================================
# Canonical string / signing
# ============================================================

def parse_query_like_signutils(url: str) -> dict[str, str]:
    parsed = urllib.parse.urlsplit(url)
    out: dict[str, str] = {}
    if not parsed.query:
        return out
    for item in parsed.query.split("&"):
        parts = item.split("=")
        if len(parts) > 1:
            out[parts[0]] = parts[1]
        elif len(parts) == 1:
            out[parts[0]] = ""
    return out


def build_canonical(
    method: str, body: str, xp_headers: dict[str, str], url: str = ""
) -> str:
    method = (method or "GET").upper()
    params = parse_query_like_signutils(url)
    for k, v in xp_headers.items():
        ku = k.upper()
        if not ku.startswith("XP-") or ku == "XP-SIGNATURE":
            continue
        params[ku] = v
    items = sorted(params.items(), key=lambda kv: kv[0])
    paramstr = "&".join(f"{k}={v}" for k, v in items)
    body_part = md5_hex(body) if body and method in ("POST", "PUT") else ""
    return f"{method}&{paramstr}&{body_part}"


def absolute_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


# ============================================================
# Authorization blob (AES-GCM)
# ============================================================

def strip_pkcs7(data: bytes, block_size: int = 16) -> bytes:
    if not data:
        return data
    pad = data[-1]
    if not (1 <= pad <= block_size):
        return data
    if data[-pad:] == bytes([pad]) * pad:
        return data[:-pad]
    return data


def decrypt_secrete_key(
    secrete_key_b64: str, device_id: str
) -> dict[int, bytes]:
    key = hashlib.md5(device_id.encode("utf-8")).digest()
    ct = b64decode_padded(secrete_key_b64)
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    decryptor = cipher.decryptor()
    pt = decryptor.update(ct) + decryptor.finalize()
    text = strip_pkcs7(pt).decode("utf-8")
    pool: dict[int, bytes] = {}
    for match in re.finditer(r'(\d+):\{"index":\d+,"key":"([^"]+)"\}', text):
        pool[int(match.group(1))] = b64decode_padded(match.group(2))
    if len(pool) != 10:
        raise ValueError(
            f"expected 10 keys in secreteKey plaintext, got {len(pool)}: "
            f"{text[:120]!r}"
        )
    return pool


def build_authorization(
    tee_id: str,
    device_id: str,
    key_index: int,
    key: bytes,
    plaintext: bytes,
    *,
    version: int = 2,
    nonce_length: int = 36,
    timestamp_ms: int | None = None,
    iv: bytes | None = None,
) -> str:
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    if iv is None:
        iv = os.urandom(12)
    encrypted = AESGCM(key).encrypt(iv, plaintext, None)
    tee_id_bytes = tee_id.encode("ascii")
    device_id_bytes = device_id.encode("ascii")
    raw = bytearray()
    raw.append(len(tee_id_bytes))
    raw += tee_id_bytes
    raw += b"OA"
    raw.append(version & 0xFF)
    raw.append(key_index & 0xFF)
    raw.append(len(device_id_bytes))
    raw += device_id_bytes
    raw.append(nonce_length & 0xFF)
    raw += int(timestamp_ms).to_bytes(8, "big")
    raw.append(len(iv))
    raw += iv
    raw += encrypted
    return base64.b64encode(bytes(raw)).decode("ascii")


def parse_xtee_blob_flexible(value: str) -> dict[str, Any]:
    raw = b64decode_padded(value)
    if len(raw) < 112 or raw[0] != 0x40 or raw[65:67] != b"OA" or raw[69] != 0x20:
        raise ValueError("not an OA XTEE blob")
    nonce_len = raw[102]
    iv_len = raw[111]
    if len(raw) < 112 + iv_len + 16:
        raise ValueError("truncated XTEE payload")
    return {
        "teeId": raw[1:65].decode("ascii"),
        "version": raw[67],
        "keyIndex": raw[68],
        "deviceId": raw[70:102].decode("ascii"),
        "nonceLength": nonce_len,
        "timestampMs": int.from_bytes(raw[103:111], "big"),
        "iv": raw[112 : 112 + iv_len],
        "encrypted": raw[112 + iv_len :],
    }


def decrypt_xtee_blob_flexible(
    value: str, key_pool: dict[int, bytes]
) -> bytes:
    """Decrypt XTEE blob — returns FULL plaintext including nonce prefix."""
    item = parse_xtee_blob_flexible(value)
    key = key_pool[item["keyIndex"]]
    return AESGCM(key).decrypt(item["iv"], item["encrypted"], None)


def decrypt_xtee_payload(
    value: str, key_pool: dict[int, bytes]
) -> bytes:
    """Decrypt XTEE blob and strip the leading nonce bytes.

    Returns the raw token bytes as the server expects them — equivalent to
    com.xiaopeng.xtee.token.TokenProcessor.getItemRawData().
    """
    item = parse_xtee_blob_flexible(value)
    key = key_pool[item["keyIndex"]]
    full = AESGCM(key).decrypt(item["iv"], item["encrypted"], None)
    nonce_len = item.get("nonceLength", 0)
    return full[nonce_len:]


# ============================================================
# RSA password encryption
# ============================================================

def rsa_encrypt_password(password: str) -> str:
    public_key = serialization.load_der_public_key(
        base64.b64decode(PROD_RSA_PUBLIC_KEY_B64)
    )
    encrypted = public_key.encrypt(password.encode("utf-8"), padding.PKCS1v15())
    return base64.encodebytes(encrypted).decode("ascii")


# ============================================================
# NetEase PToken generation
# ============================================================

def proto_varint(value: int) -> bytes:
    out = bytearray()
    value = int(value)
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def proto_field_varint(number: int, value: int) -> bytes:
    return proto_varint((number << 3) | 0) + proto_varint(value)


def proto_field_bytes(number: int, value: bytes | str) -> bytes:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return proto_varint((number << 3) | 2) + proto_varint(len(value)) + value


def pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    padder = sym_padding.PKCS7(block_size * 8).padder()
    return padder.update(data) + padder.finalize()


def gzip_deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(
        level=-1,
        method=zlib.DEFLATED,
        wbits=31,
        memLevel=9,
        strategy=zlib.Z_DEFAULT_STRATEGY,
    )
    return compressor.compress(data) + compressor.flush(zlib.Z_FINISH)


def aes_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(pkcs7_pad(data, 16)) + encryptor.finalize()


def random_netease_string(length: int) -> str:
    alphabet = "0123456789abcdefghijkmlnopqrstuvwxyzABCDEFGHIJKMLNOPQRSTUVWXYZ"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def build_netease_ptoken(
    risk_payload: bytes, *, timestamp_ms: int | None = None
) -> str:
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    aes_key = random_netease_string(16).encode("ascii")
    iv = secrets.token_bytes(16)
    public_key = serialization.load_der_public_key(
        base64.b64decode(NETHT_RSA_PUBLIC_KEY_DER_B64)
    )
    encrypted_session = public_key.encrypt(aes_key + iv, padding.PKCS1v15())
    compressed = gzip_deflate(risk_payload)
    encrypted_payload = aes_cbc_encrypt(aes_key, iv, compressed)
    meta = b"".join([
        proto_field_varint(1, 300),
        proto_field_varint(2, timestamp_ms),
        proto_field_bytes(3, random_netease_string(32)),
        proto_field_bytes(4, "zB10AAkJBwQCAAQNCgoIBg=="),
        proto_field_varint(5, 1),
        proto_field_varint(6, 1),
        proto_field_varint(7, 3),
        proto_field_varint(8, 2),
        proto_field_bytes(9, "5.6.8"),
        proto_field_varint(10, 36),
    ])
    payload = b"".join([
        proto_field_varint(1, 5),
        proto_field_varint(2, 1),
        proto_field_bytes(3, base64.b64encode(encrypted_session)),
        proto_field_bytes(4, encrypted_payload),
    ])
    raw = proto_field_bytes(1, meta) + proto_field_bytes(2, payload)
    return base64.urlsafe_b64encode(raw).decode("ascii")


def make_ptoken(timestamp_ms: int | None = None) -> str:
    """Generate a PToken from the embedded template."""
    risk_payload = base64.b64decode(EMBEDDED_PTOKEN_TEMPLATE_B64)
    return build_netease_ptoken(risk_payload, timestamp_ms=timestamp_ms)


# ============================================================
# SSL / HTTP
# ============================================================

# The mTLS files are read from disk (see certs/README.md) — the client key is
# no longer embedded in source. This is the fallback list; mqtt_client owns
# CERT_FILES in the working copy, and the presence check is delegated to it
# whenever it can be imported.
_CERT_FILES = ("ca-chain.crt", "client-cert-0.pem", "client-key.pem")


def _missing_cert_files(cert_dir: str) -> list[str]:
    """Names of the required mTLS files missing from ``cert_dir``.

    mqtt_client owns this list (config_flow validates against it), but it
    imports paho at module scope, so it is imported lazily: this module is
    deliberately loadable standalone by tests/tools, where paho is absent.
    """
    try:
        from .mqtt_client import missing_cert_files
    except ImportError:  # standalone load, or a copy predating that helper
        return [
            name
            for name in _CERT_FILES
            if not os.path.isfile(os.path.join(cert_dir, name))
        ]
    return [path.name for path in missing_cert_files(cert_dir)]


def load_client_cert(
    ctx: ssl.SSLContext,
    cert_dir: str,
    key_password: str = "",
) -> None:
    """Load the REST client's mTLS cert/key from ``cert_dir`` into ``ctx``.

    Raises:
        FileNotFoundError: naming the missing file(s), the directory searched,
            and where to read about obtaining them.
    """
    missing = _missing_cert_files(cert_dir)
    if missing:
        raise FileNotFoundError(
            f"XPENG mTLS certificate file(s) missing from {cert_dir}: "
            f"{', '.join(missing)}. "
            f"Required files: {', '.join(_CERT_FILES)}. "
            "See custom_components/xpeng/certs/README.md for how to obtain "
            "them, or point the integration's 'Certificate directory' option "
            "at the directory holding them."
        )
    cert_file = os.path.join(cert_dir, "client-cert-0.pem")
    key_file = os.path.join(cert_dir, "client-key.pem")
    password = key_password.encode("utf-8") if key_password else None
    try:
        ctx.load_cert_chain(certfile=cert_file, keyfile=key_file, password=password)
    except ssl.SSLError:
        # The on-disk key is an unencrypted SEC1 EC key; retry bare rather than
        # failing a caller that passed a password for the old embedded blob.
        ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)


def make_ssl_context(
    use_mtls: bool,
    cert_path: str = "",
    key_path: str = "",
    key_password: str = "",
    cert_dir: str = "",
    public_ca: bool = False,
) -> ssl.SSLContext:
    """Build the TLS context for XPENG REST calls.

    The peer is authenticated: the XPENG CA chain is loaded from ``cert_dir``
    and a verified certificate with a matching hostname is required. The
    default LoginBaseUrl host (apps-global.xiaopeng.com) is covered by that
    chain. Tenants that use a different gateway host need the directory to hold
    the matching CA chain.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    if use_mtls:
        if cert_path and key_path and os.path.isfile(cert_path) and os.path.isfile(key_path):
            pw = key_password.encode("utf-8") if key_password else None
            try:
                ctx.load_cert_chain(certfile=cert_path, keyfile=key_path, password=pw)
            except ssl.SSLError:
                ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
        else:
            load_client_cert(ctx, cert_dir, key_password)

    if not cert_dir:
        # A caller that supplied only the explicit cert/key override is assumed
        # to keep the CA chain beside it.
        cert_dir = os.path.dirname(cert_path)
    ca_file = os.path.join(cert_dir, "ca-chain.crt")
    if not cert_dir or not os.path.isfile(ca_file):
        raise FileNotFoundError(
            f"XPENG CA chain 'ca-chain.crt' not found in "
            f"{cert_dir or '<no certificate directory>'}. "
            "See custom_components/xpeng/certs/README.md for how to obtain it, "
            "or point the integration's 'Certificate directory' option at the "
            "directory holding it."
        )
    # Order matters: the trust anchor is loaded while the context still has its
    # PROTOCOL_TLS_CLIENT default, then verification is demanded explicitly.
    ctx.load_verify_locations(cafile=ca_file)

    if public_ca:
        # Some XPENG endpoints are NOT signed by the XPENG CA chain.
        # msgcenter-oversea-eur.xiaopeng.com is fronted by Aliyun's WAF and
        # serves a public CA, so pinning only the XPENG chain made every call
        # to it die with:
        #   URLError: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED]
        #             certificate verify failed: unable to get local issuer>
        # Trust the system store *in addition to* the XPENG chain - never
        # instead of it - so one context covers both kinds of peer without
        # weakening verification for the endpoints that the pinned chain does
        # cover. check_hostname and CERT_REQUIRED still apply either way.
        try:
            ctx.load_default_certs(ssl.Purpose.SERVER_AUTH)
        except Exception:  # pragma: no cover - platform dependent
            _LOGGER.debug("Could not load system CA certificates", exc_info=True)

    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = True
    return ctx


def header_value(headers: dict[str, str], name: str) -> str:
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v
    return ""


def decode_response_bytes(body: bytes, headers: dict[str, str]) -> str:
    import gzip as _gzip

    encoding = header_value(headers, "Content-Encoding").lower()
    decoded = body
    try:
        if "gzip" in encoding:
            decoded = _gzip.decompress(body)
        elif "deflate" in encoding:
            try:
                decoded = zlib.decompress(body)
            except zlib.error:
                decoded = zlib.decompress(body, -zlib.MAX_WBITS)
    except Exception:
        decoded = body
    return decoded.decode("utf-8", errors="replace")


def send_request(
    url: str,
    method: str,
    body: str,
    headers: dict[str, str],
    ssl_ctx: ssl.SSLContext,
    timeout: float = 30.0,
) -> dict[str, Any]:
    data = body.encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
            resp_headers = dict(resp.headers.items())
            body_bytes = resp.read()
            return {
                "status": resp.status,
                "reason": resp.reason,
                "headers": resp_headers,
                "body": decode_response_bytes(body_bytes, resp_headers),
                "url": resp.url,
            }
    except urllib.error.HTTPError as e:
        body_bytes = e.read() if e.fp else b""
        resp_headers = dict(e.headers.items()) if e.headers else {}
        return {
            "status": e.code,
            "reason": e.reason,
            "headers": resp_headers,
            "body": decode_response_bytes(body_bytes, resp_headers),
            "url": url,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ============================================================
# Token helpers
# ============================================================

def strip_token_prefix(value: str) -> str:
    marker = value.find("eyJ")
    return value[marker:] if marker >= 0 else value


def normalize_login_token_for_auth(
    value: str, key_pool: dict[int, bytes]
) -> str:
    value = (value or "").strip()
    if not value:
        return value
    try:
        plain = decrypt_xtee_blob_flexible(value, key_pool).decode(
            "utf-8", errors="replace"
        )
        return strip_token_prefix(plain)
    except Exception:
        return strip_token_prefix(value)


def load_login_tokens(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        wrapper = json.load(f)
    if isinstance(wrapper.get("json"), dict):
        return wrapper["json"]
    text = wrapper.get("jsonText") or wrapper.get("plaintext") or ""
    start = text.find("{")
    if start < 0:
        raise ValueError(f"No JSON token object found in {path}")
    return json.loads(text[start:])


# ============================================================
# Tenant cache
# ============================================================

def load_tenant_cache(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        tenant = json.load(f)
    tenant["_key_pool"] = {
        int(k): base64.b64decode(v) for k, v in tenant["keys"].items()
    }
    return tenant


def deserialize_tenant(data: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct tenant dict with _key_pool from serialized config entry data."""
    tenant = dict(data)
    tenant["_key_pool"] = {
        int(k): base64.b64decode(v) for k, v in tenant.get("keys", {}).items()
    }
    return tenant


def save_tenant_cache(path: str, tenant: dict[str, Any]) -> None:
    saveable = {k: v for k, v in tenant.items() if not k.startswith("_")}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(saveable, f, indent=2, ensure_ascii=False)


# ============================================================
# Header / config helpers
# ============================================================

# Process-wide cert-dir override. The MQTT client takes cert_dir explicitly,
# but the REST helpers are called with config=None throughout, so the chosen
# directory has to be published here or the REST path would silently fall back
# to the packaged certs and fail when the user keeps theirs elsewhere.
_CERT_DIR_OVERRIDE: str = ""

# Process-wide configured-device-id override, mirroring _CERT_DIR_OVERRIDE. The
# REST helpers are also called with config=None throughout, and the shipped
# default deliberately no longer carries a `di=` segment, so this install's
# minted identity has to be published here or the REST path could not sign at
# all. `set_configured_device_id()` is called by __init__.py at entry setup.
_CONFIGURED_DEVICE_ID_OVERRIDE: str = ""

#: The device id sits in 32 ASCII bytes at offset 70-102 of the XTEE
#: authorization blob (`build_authorization` / `parse_xtee_blob_flexible`), so a
#: 32-character hex string from `mint_device_id()` fits it exactly.
DEVICE_ID_MAX_LENGTH: int = 32


def set_cert_dir(cert_dir: str) -> None:
    """Publish the configured certificate directory for the REST client."""
    global _CERT_DIR_OVERRIDE
    _CERT_DIR_OVERRIDE = (cert_dir or "").strip()


def set_configured_device_id(device_id: str) -> None:
    """Publish this install's device id for the REST client.

    Same pattern (and the same rationale) as :func:`set_cert_dir`: the REST
    helpers take `config=None`, so the id chosen at entry setup has to live in a
    process-wide override. An explicit `ConfiguredDeviceId` in a call's config
    dict still wins, which is what the config flow relies on before an entry
    exists.
    """
    global _CONFIGURED_DEVICE_ID_OVERRIDE
    _CONFIGURED_DEVICE_ID_OVERRIDE = (device_id or "").strip()


def mint_device_id() -> str:
    """Mint a fresh per-install device id: exactly 32 lowercase hex chars.

    The length is not cosmetic — it is the wire format. See
    :data:`DEVICE_ID_MAX_LENGTH`.
    """
    return secrets.token_hex(16)


def _get_config(config: dict[str, str] | None) -> dict[str, str]:
    """Merge user config over defaults."""
    cfg = dict(DEFAULT_APP_CONFIG)
    # Allow the integration to point the TLS material at a user-chosen
    # directory without threading a config dict through every call site.
    # Set via set_cert_dir(); falls back to the packaged certs/ directory.
    if _CERT_DIR_OVERRIDE:
        cfg["CertDir"] = _CERT_DIR_OVERRIDE
    # Same idea for the device identity: published by set_configured_device_id()
    # at entry setup. There is no shipped fallback any more, so when this is
    # unset *and* the caller passes no id, _get_device_id() raises rather than
    # silently impersonating whatever device the package was built with.
    if _CONFIGURED_DEVICE_ID_OVERRIDE:
        cfg["ConfiguredDeviceId"] = _CONFIGURED_DEVICE_ID_OVERRIDE
    if config:
        cfg.update(config)
    return cfg


def _parse_extra_headers(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def _device_id_from_tenant(tenant: dict[str, Any] | None) -> str:
    """The device id the tenant was fetched under, if it recorded one."""
    if not isinstance(tenant, dict):
        return ""
    return str(tenant.get("deviceId") or "").strip()


def _cfg_with_tenant_identity(
    cfg: dict[str, str], tenant: dict[str, Any] | None
) -> dict[str, str]:
    """
    Force `cfg` to sign as the device the tenant belongs to.

    The access/refresh envelopes are sealed with the tenant's key pool AND the
    requests are signed as a specific device, so the two must agree. When HA calls
    this module it passes `config=None`, so without this the code would fall back
    to the shipped default device id and every MQTT/bootstrap call would answer
    `12010104 Login status expired` — the same error as genuinely dead tokens.

    An explicit ConfiguredDeviceId/LoginDeviceId still wins, which is what the
    onboarding tool relies on before a tenant even exists. The *process-wide*
    override does NOT win: it is just whichever entry was set up last, so with two
    config entries honouring it made one car's requests sign as the other car's
    device — every call answered 12010104 and the two entries fought (issue #11).
    The tenant is the identity the pair was actually sealed with.
    """
    device_id = _device_id_from_tenant(tenant)
    if not device_id:
        return cfg
    explicit = str(cfg.get("ConfiguredDeviceId", "") or "").strip()
    if explicit and explicit != _CONFIGURED_DEVICE_ID_OVERRIDE:
        return cfg
    if str(cfg.get("LoginDeviceId", "") or "").strip():
        return cfg
    merged = dict(cfg)
    merged["ConfiguredDeviceId"] = device_id
    return merged


def _get_device_id(config: dict[str, str]) -> str:
    """
    The device identity this install signs as.

    Portable onboarding REQUIRES this to be per-install: the device id is signed
    into the XTEE authorization, sent in the User-Agent, and embedded in the MQTT
    clientId that `createMqttAccount` issues (`xmart:app:<uid>:<deviceId>`). Two
    installs sharing one id are impersonating each other, so every onboarding run
    must generate its own.

    Resolution order: the explicit `ConfiguredDeviceId`, then `LoginDeviceId`,
    then the `di=` segment of `Client`. The shipped default no longer carries a
    `di=` segment (it used to pin the author's device), so in practice the id
    comes from the config dict the config flow passes, or from the process-wide
    override `set_configured_device_id()` publishes at entry setup — both are
    minted by `mint_device_id()`.

    Raises ValueError when nothing carries an id: a caller that reaches this
    without one is about to impersonate a device it does not own, and the old
    silent fallback to the shipped `di=` is exactly what this change removed.
    """
    for key in ("ConfiguredDeviceId", "LoginDeviceId"):
        override = str(config.get(key, "") or "").strip()
        if override:
            if len(override) > DEVICE_ID_MAX_LENGTH:
                raise ValueError(
                    f"{key} is {len(override)} characters; the XTEE "
                    f"authorization blob only has room for "
                    f"{DEVICE_ID_MAX_LENGTH}. Use mint_device_id(), which "
                    "returns exactly 32 hex characters."
                )
            return override
    di = extract_client_segment(config.get("Client", ""), "di")
    if not di:
        raise ValueError(
            "No device id configured. The shipped default no longer pins one, "
            "so this install must mint its own with xpeng_api.mint_device_id() "
            "and pass it as ConfiguredDeviceId (or publish it once with "
            "set_configured_device_id()). The HA integration does this "
            "automatically at first run; a standalone caller that reaches this "
            "point has skipped that step."
        )
    return di


def _client_with_device_id(config: dict[str, str], device_id: str) -> str:
    """The Client string with its `di=` segment forced to `device_id`."""
    return patch_client_segment(config.get("Client", ""), "di", device_id)


def _user_agent_for(config: dict[str, str], device_id: str) -> str:
    """
    The Android User-Agent, carrying the SAME device id as the signature.

    This previously hardcoded the shipped device id while the signature used the
    configured one, so overriding the identity produced a request whose
    User-Agent contradicted its own signature.
    """
    return (
        "com.xiaopeng.globalcarinfo/3.11.0 "
        "(8801; SM-G975F; Android; 16; samsung; "
        f"{device_id}; androidstore)"
    )


def base_wire_headers(config: dict[str, str] | None = None) -> dict[str, str]:
    cfg = _get_config(config)
    headers = {
        "Accept-Encoding": "gzip, deflate",
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": _user_agent_for(cfg, _get_device_id(cfg)),
        "Mockfilter": "1",
        "Language": "de-DE",
        "Country": "DE",
        "Client-Type": "APP",
    }
    extra = _parse_extra_headers(cfg.get("ExtraHeaders", ""))
    for k, v in extra.items():
        if not k.lower().startswith("xp-") and k.lower() != "content-length":
            headers.setdefault(k, v)
    return headers


def prepare_request_values(
    config: dict[str, str] | None = None,
) -> dict[str, Any]:
    cfg = _get_config(config)
    nonce, ts_ms = gen_nonce_and_ts()
    device_id = _get_device_id(cfg)
    client = patch_client_segment(cfg.get("Client", ""), "di", device_id)
    client = patch_client_t(client, ts_ms)
    return {
        "deviceId": device_id,
        "client": client,
        "nonce": nonce,
        "timestampMs": ts_ms,
    }


# ============================================================
# Signing: tenant headers (no XTEE, for fetch_tenant)
# ============================================================

def build_tenant_headers(
    config: dict[str, str] | None,
    url: str,
    body: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    cfg = _get_config(config)
    rv = prepare_request_values(cfg)
    headers = base_wire_headers(cfg)
    xp = {
        "Xp-Appid": cfg.get("AppId", ""),
        "Xp-Brand": cfg.get("Brand", "XPENG"),
        "Xp-Client-Encoding": cfg.get("ClientEncoding", "None"),
        "Xp-Client-Type": cfg.get("ClientType", "1"),
        "Xp-Uid": "0",
        "Xp-Client": rv["client"],
        "Xp-Nonce": rv["nonce"],
        "Xp-Encryption-Type": cfg.get("EncryptionType", "2"),
        "Xp-Tee-Version": cfg.get("LoginTenantVersion", "2"),
    }
    headers.update(xp)
    canonical = build_canonical("POST", body, xp, url)
    secret = cfg.get("Secret", APP_SECRET) or APP_SECRET
    signature = hmac_hex(secret, canonical)
    headers["Xp-Signature"] = signature
    return headers, {**rv, "canonical": canonical, "signature": signature}


# ============================================================
# Signing: XTEE signed headers (for login steps)
# ============================================================

def build_xtee_signed_headers(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    url: str,
    method: str,
    body: str,
    *,
    ptoken: str | None = None,
    xid: str = "",
    request_values: dict[str, Any] | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    cfg = _get_config(config)
    rv = request_values or prepare_request_values(cfg)
    headers = base_wire_headers(cfg)
    headers.update({
        "Xp-Tee-Id": tenant["teeId"],
        "Xp-Tee-Version": str(tenant.get("version", 2)),
        "Xp-Client-Type": cfg.get("ClientType", "1"),
        "Xp-Appid": cfg.get("AppId", ""),
        "Xp-Brand": cfg.get("Brand", "XPENG"),
        "Xp-Client-Encoding": cfg.get("ClientEncoding", "None"),
        "Xp-Uid": "0",
        "Xp-Client": rv["client"],
        "Xp-Nonce": rv["nonce"],
        "Xp-Encryption-Type": cfg.get("EncryptionType", "2"),
    })
    if xid is not None:
        headers["Xp-Xid"] = xid
    if ptoken is not None:
        headers["Xp-Ptoken"] = ptoken
        headers["Xp-Ptokentype"] = cfg.get("LoginPTokenType", "1")

    # Inner xpSign (without Authorization)
    inner_headers = dict(headers)
    inner_headers.pop("Xp-Authorization", None)
    inner_canonical = build_canonical(method, body, inner_headers, url)
    secret = cfg.get("Secret", APP_SECRET) or APP_SECRET
    inner_xpsign = hmac_hex(secret, inner_canonical)

    # Build authorization plaintext as raw bytes (matches Android client)
    nonce_bytes = str(uuid.uuid4()).encode("utf-8")
    head_data = base64.b64encode(
        compact_json({"xpSign": inner_xpsign}).encode("utf-8")
    )
    auth_plain = nonce_bytes + head_data

    # Encrypt into XTEE blob
    key_pool = tenant["_key_pool"]
    override = cfg.get("LoginKeyIndex", "").strip()
    key_index = int(override) if override else secrets.randbelow(10)
    if key_index not in key_pool:
        raise KeyError(f"key index {key_index} not in tenant key pool")
    headers["Xp-Authorization"] = build_authorization(
        tenant["teeId"],
        rv["deviceId"],
        key_index,
        key_pool[key_index],
        auth_plain,
        version=int(tenant.get("version", 2)),
        nonce_length=len(nonce_bytes),
        timestamp_ms=rv["timestampMs"],
    )

    # Outer signature (with Authorization)
    outer_canonical = build_canonical(method, body, headers, url)
    signature = hmac_hex(secret, outer_canonical)
    headers["Xp-Signature"] = signature

    return headers, {
        **rv,
        "innerXpSign": inner_xpsign,
        "authorizationPlain": auth_plain.decode("utf-8", errors="replace"),
        "keyIndex": key_index,
        "canonical": outer_canonical,
        "signature": signature,
    }


# ============================================================
# Signing: MQTT account headers (authenticated, with tokens)
# ============================================================

def build_mqtt_signed_headers(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    url: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    cfg = _cfg_with_tenant_identity(_get_config(config), tenant)
    rv = prepare_request_values(cfg)
    body = ""
    uid = str(tokens.get("uid") or "")
    if not uid:
        raise ValueError("Login token JSON has no uid")
    raw_access = tokens.get("accessToken") or ""
    raw_refresh = tokens.get("refreshToken") or ""
    if not raw_access or not raw_refresh:
        raise ValueError("Login token JSON has no accessToken/refreshToken")

    key_pool = tenant["_key_pool"]
    # Decrypt tokens to raw bytes (strips nonce prefix like Android client)
    access_plain = decrypt_xtee_payload(raw_access, key_pool)
    refresh_plain = decrypt_xtee_payload(raw_refresh, key_pool)

    headers = base_wire_headers(cfg)
    headers["Content-Length"] = "0"
    headers.update({
        "Xp-Tee-Id": tenant["teeId"],
        "Xp-Tee-Version": str(tenant.get("version", 2)),
        "Xp-Client-Type": cfg.get("ClientType", "1"),
        "Xp-Appid": cfg.get("AppId", ""),
        "Xp-Brand": cfg.get("Brand", "XPENG"),
        "Xp-Client-Encoding": cfg.get("ClientEncoding", "None"),
        "Xp-Uid": uid,
        "Xp-Client": rv["client"],
        "Xp-Nonce": rv["nonce"],
        "Xp-Encryption-Type": cfg.get("EncryptionType", "2"),
    })

    secret = cfg.get("Secret", APP_SECRET) or APP_SECRET
    inner_canonical = build_canonical("POST", body, headers, url)
    inner_xpsign = hmac_hex(secret, inner_canonical)

    # Auth plaintext as raw bytes with token extension (matches Android client)
    nonce_bytes = str(uuid.uuid4()).encode("utf-8")
    head_data = base64.b64encode(
        compact_json({"xpSign": inner_xpsign}).encode("utf-8")
    )
    extension = (b"&accessToken=" + access_plain
                 + b"&refreshToken=" + refresh_plain)
    auth_plain = nonce_bytes + head_data + extension

    override = cfg.get("LoginKeyIndex", "").strip()
    key_index = int(override) if override else secrets.randbelow(10)
    if key_index not in key_pool:
        raise KeyError(f"key index {key_index} not in tenant key pool")
    headers["Xp-Authorization"] = build_authorization(
        tenant["teeId"],
        rv["deviceId"],
        key_index,
        key_pool[key_index],
        auth_plain,
        version=int(tenant.get("version", 2)),
        nonce_length=len(nonce_bytes),
        timestamp_ms=rv["timestampMs"],
    )
    outer_canonical = build_canonical("POST", body, headers, url)
    signature = hmac_hex(secret, outer_canonical)
    headers["Xp-Signature"] = signature

    return headers, {
        **rv,
        "uid": uid,
        "innerXpSign": inner_xpsign,
        "keyIndex": key_index,
        "canonical": outer_canonical,
        "signature": signature,
    }


# ============================================================
# High-level API calls
# ============================================================

def _send_api_request(
    config: dict[str, str] | None,
    url: str,
    method: str,
    body: str,
    headers: dict[str, str],
    public_ca: bool = False,
) -> dict[str, Any]:
    """Send an API request with mTLS."""
    cfg = _get_config(config)
    ssl_ctx = make_ssl_context(
        use_mtls=cfg.get("UseMTLS", "1") == "1",
        cert_path=cfg.get("ClientCertPath", ""),
        key_path=cfg.get("ClientKeyPath", ""),
        key_password=cfg.get("ClientKeyPassword", CLIENT_KEY_PASSWORD),
        cert_dir=cfg.get("CertDir", DEFAULT_CERT_DIR),
        public_ca=public_ca,
    )
    return send_request(url, method, body, headers, ssl_ctx)


def _extract_xid(resp: dict[str, Any]) -> str:
    if "error" in resp:
        return ""
    for k, v in resp.get("headers", {}).items():
        if k.lower() == "xp-xid" and v:
            return v
    cookie = ""
    for k, v in resp.get("headers", {}).items():
        if k.lower() == "set-cookie":
            cookie = v
            break
    m = re.search(r"XP-Xid=([^;]+)", cookie)
    return m.group(1) if m else ""


def fetch_tenant(
    config: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Fetch tenant secrets (teeId + key pool) from the XPENG API."""
    cfg = _get_config(config)
    base_url = cfg.get("LoginBaseUrl", "https://apps-global.xiaopeng.com")
    url = absolute_url(base_url, "/gateway/v1/secure/teeIndiv4OverseaApp")
    version = cfg.get("LoginTenantVersion", "2")
    body = compact_json({"version": version})
    headers, debug = build_tenant_headers(cfg, url, body)

    _LOGGER.debug("Fetching tenant from %s", url)
    resp = _send_api_request(cfg, url, "POST", body, headers)

    if "error" in resp:
        raise ConnectionError(f"Tenant fetch failed: {resp['error']}")
    status = int(resp.get("status", 0))
    if status >= 400:
        raise ConnectionError(
            f"Tenant fetch HTTP {status}: {resp.get('body', '')[:200]}"
        )

    parsed = json.loads(resp.get("body", "{}"))
    data = parsed.get("data") or {}
    tee_id = data["teeId"]
    secrete_key = data["secreteKey"]
    tenant_version = int(data.get("version", version))
    device_id = debug["deviceId"]

    key_pool = decrypt_secrete_key(secrete_key, device_id)
    tenant = {
        "fetchedAt": int(time.time()),
        "deviceId": device_id,
        "teeId": tee_id,
        "version": tenant_version,
        "secreteKey": secrete_key,
        "keys": {str(k): b64_bytes(v) for k, v in sorted(key_pool.items())},
        "_key_pool": key_pool,
    }
    _LOGGER.info("Tenant fetched: teeId=%s, %d keys", tee_id, len(key_pool))
    return tenant


def create_mqtt_account(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
) -> dict[str, Any]:
    """Call createMqttAccount API and return MQTT credentials.

    Returns dict with keys: clientId, password, serverSsl, expireTime, username
    """
    cfg = _get_config(config)
    base_url = cfg.get("LoginBaseUrl", "https://apps-global.xiaopeng.com")
    path = cfg.get("MqttPath", "/biz/v5/mqtt/createMqttAccount/app")
    url = absolute_url(base_url, path)

    headers, debug = build_mqtt_signed_headers(cfg, tenant, tokens, url)
    _LOGGER.debug("Calling createMqttAccount at %s", url)
    resp = _send_api_request(cfg, url, "POST", "", headers)

    if "error" in resp:
        raise ConnectionError(f"createMqttAccount failed: {resp['error']}")

    # Parse BEFORE the status check. XPENG answers an aged-out access token with
    # HTTP 403 and a body of {"code":12010101,"msg":"Login status expired..."},
    # so a status check placed above this turns the one condition we can recover
    # from into an anonymous ConnectionError and the recovery never fires. That
    # is not hypothetical: it is why a fresh install ran refresh after refresh
    # without recovering (observed 2026-10-07). A body that will not parse simply
    # falls through to the status check below.
    parsed: dict[str, Any] = {}
    try:
        parsed = json.loads(resp.get("body", "") or "{}")
    except (ValueError, TypeError):
        parsed = {}
    if is_token_expired_response(parsed):
        raise LoginExpiredError(
            int(parsed.get("code")),
            parsed.get("msg") or parsed.get("desc") or "",
        )

    status = int(resp.get("status", 0))
    if status >= 400:
        raise ConnectionError(
            f"createMqttAccount HTTP {status}: {resp.get('body', '')[:200]}"
        )

    data = parsed.get("data")

    # Response may have encrypted sData
    if isinstance(data, dict) and data.get("sData"):
        key_pool = tenant["_key_pool"]
        plain = decrypt_xtee_blob_flexible(data["sData"], key_pool).decode(
            "utf-8", errors="replace"
        )
        start = plain.find("{")
        if start >= 0:
            data = json.loads(plain[start:])

    if not isinstance(data, dict):
        raise ValueError(f"Unexpected response data: {str(data)[:200]}")

    account = data.get("mqttAccount") or data
    if not isinstance(account, dict) or "clientId" not in account:
        raise ValueError(f"No mqttAccount in response: {str(data)[:200]}")

    now_ms = int(time.time() * 1000)
    expire_ms = account.get("expireTime")
    expire_in_sec = (
        round((int(expire_ms) - now_ms) / 1000) if expire_ms else None
    )

    _LOGGER.info(
        "MQTT account obtained: clientId=%s, expires_in=%ss",
        account.get("clientId", "")[:30],
        expire_in_sec,
    )
    return {
        "clientId": account.get("clientId", ""),
        "username": account.get("username", ""),
        "password": account.get("password", ""),
        "serverSsl": account.get("serverSsl", ""),
        "expireTime": account.get("expireTime"),
        "expireInSec": expire_in_sec,
    }


# ============================================================
# Login flow (for initial token acquisition)
# ============================================================

def _login_signed_step(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    label: str,
    path: str,
    body: str,
    *,
    ptoken: str | None = None,
    xid: str = "",
) -> dict[str, Any]:
    """Execute a single login flow step with XTEE signing."""
    cfg = _get_config(config)
    base_url = cfg.get("LoginBaseUrl", "https://apps-global.xiaopeng.com")
    url = absolute_url(base_url, path)
    rv = prepare_request_values(cfg)

    if ptoken is None:
        ptoken = make_ptoken(timestamp_ms=rv["timestampMs"])

    headers, debug = build_xtee_signed_headers(
        cfg, tenant, url, "POST", body,
        ptoken=ptoken, xid=xid, request_values=rv,
    )
    _LOGGER.debug("Login step '%s' -> %s", label, url)
    resp = _send_api_request(cfg, url, "POST", body, headers)

    if "error" in resp:
        raise ConnectionError(f"{label} failed: {resp['error']}")
    status = int(resp.get("status", 0))
    if status >= 400:
        raise ConnectionError(
            f"{label} HTTP {status}: {resp.get('body', '')[:200]}"
        )

    new_xid = _extract_xid(resp)
    return {
        "resp": resp,
        "xid": new_xid,
        "body_json": json.loads(resp.get("body", "{}")),
    }


def login_get_state(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    email: str,
) -> dict[str, Any]:
    body = compact_gson_json({"email": email})
    return _login_signed_step(
        config, tenant, "getUserStateByEmail",
        "/sso/app/v7/email/getUserStateByEmail", body, xid="",
    )


def login_send_code(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    email: str,
    *,
    xid: str = "",
    sms_type: str = "88",
) -> dict[str, Any]:
    cfg = _get_config(config)
    sms_type = cfg.get("LoginSmsType", sms_type)
    body = compact_gson_json({"email": email, "smsType": sms_type})
    return _login_signed_step(
        config, tenant, "email/code",
        "/verification/app/v7/email/code", body, xid=xid,
    )


def login_validate_code(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    email: str,
    code: str,
    *,
    xid: str = "",
    sms_type: str = "88",
) -> dict[str, Any]:
    cfg = _get_config(config)
    sms_type = cfg.get("LoginSmsType", sms_type)
    body = compact_gson_json({"email": email, "code": code, "smsType": sms_type})
    return _login_signed_step(
        config, tenant, "email/validate",
        "/verification/app/v4/email/validate", body,
        ptoken=None, xid=xid,
    )


# Login-stage business codes worth naming.
#
# 12040101 and 12040106 are the two captcha challenges. The web SSO client
# branches on exactly this pair and runs its NetEase Yidun path for either;
# this module previously knew only the first, and only as an unexplained error.
# See DISTRIBUTION_PLAN.md §5b.
LOGIN_CAPTCHA_CODES = frozenset({12040101, 12040106})

_LOGIN_CODE_MESSAGES = {
    12040101: "XPENG is asking for a solved captcha (NetEase Yidun) before login",
    12040106: "XPENG is asking for a solved captcha (NetEase Yidun, second code) before login",
}


def describe_login_failure(parsed: dict[str, Any]) -> str:
    """
    A sentence for a failed login step, from whatever the server returned.

    Written because the previous code reported every single failure —
    captcha, wrong password, locked account, expired code — as the same
    unhelpful "No sData in login response", which hid the real cause.
    """
    code = parsed.get("code")
    if code in LOGIN_CAPTCHA_CODES:
        return f"{_LOGIN_CODE_MESSAGES[code]} (code={code})"
    for field in ("desc", "msg"):
        text = str(parsed.get(field) or "").strip()
        if text and text != "None":
            return f"login failed: code={code} {text}"
    return f"login failed: code={code} (no message; raw={str(parsed)[:160]})"


def mint_antibot_token() -> str | None:
    """
    Mint a NetEase Yidun anti-bot token, or None if this host cannot.

    `guardian.py` sits next to this file and is loaded by path rather than
    import, because this module is used three different ways — as part of the
    Home Assistant package, as a standalone script, and loaded by spec from the
    test tool — and only one of those has a package to import from.

    Never raises. A host without Node or Chromium should degrade to "no token"
    and let the caller see XPENG's own captcha error, not a stack trace about a
    missing runtime.
    """
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent / "guardian.py"
    if not path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("_xpeng_guardian", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.try_mint_token()
    except Exception as err:  # noqa: BLE001 — never let this break a login
        _LOGGER.debug("Anti-bot token unavailable: %s", err)
        return None


def login_password(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    email: str,
    password: str,
    *,
    xid: str = "",
    ptoken: str | None = None,
    auto_ptoken: bool = True,
) -> dict[str, Any]:
    """
    Password login. Returns tokens dict with uid, accessToken, refreshToken.

    `ptoken` is the anti-bot token, and this is the one login step that
    *enforces* the captcha — `getUserStateByEmail`, the code send and the code
    validation all let a request through without one, but this answers
    `12040106` without a valid token. Verified live 2025-09-30: a NetEase
    Guardian token turns that into an ordinary password verdict.

    When `ptoken` is not supplied and `auto_ptoken` is on, one is minted before
    the attempt and re-minted once if XPENG still asks for a captcha — the
    token is short-lived, so a stale one is the likeliest cause of a challenge
    that should have passed.
    """
    encrypted_password = rsa_encrypt_password(password)
    body = compact_gson_json({"email": email, "password": encrypted_password})

    if ptoken is None and auto_ptoken:
        ptoken = mint_antibot_token()
        if ptoken:
            _LOGGER.debug("Minted an anti-bot token for pwdLogin")

    result = _login_signed_step(
        config, tenant, "pwdLogin",
        "/sso/app/v7/email/pwdLogin", body, xid=xid, ptoken=ptoken,
    )
    parsed = result["body_json"]

    # One retry with a fresh token. A challenge on a request that already
    # carried one means it was stale or refused, and reminting is cheaper than
    # making the user solve anything.
    if parsed.get("code") in LOGIN_CAPTCHA_CODES and auto_ptoken:
        _LOGGER.info("pwdLogin asked for a captcha; retrying with a fresh anti-bot token")
        fresh = mint_antibot_token()
        if fresh:
            result = _login_signed_step(
                config, tenant, "pwdLogin",
                "/sso/app/v7/email/pwdLogin", body, xid=xid, ptoken=fresh,
            )
            parsed = result["body_json"]

    code = parsed.get("code")
    data = parsed.get("data")

    # Surface the business code first. Everything below this line used to be
    # unreachable when the server said "no", so the caller could not tell a
    # captcha challenge from a typo in the password.
    if code not in (0, 200):
        raise ValueError(describe_login_failure(parsed))

    _LOGGER.debug(
        "pwdLogin responded: code=%s data_keys=%s",
        code,
        sorted(data) if isinstance(data, dict) else type(data).__name__,
    )

    # The tokens arrive either sData-encrypted or as plain JSON, depending on
    # what the server decides. Handle both, the way get_authenticated_data and
    # refresh_login_tokens already do — this function was the only one that
    # insisted on sData and failed on everything else.
    tokens: Any = None
    if isinstance(data, dict) and data.get("sData"):
        key_pool = tenant["_key_pool"]
        plain = decrypt_xtee_blob_flexible(
            str(data["sData"]), key_pool
        ).decode("utf-8", errors="replace")
        json_start = plain.find("{")
        if json_start < 0:
            raise ValueError("No JSON in decrypted sData")
        tokens = json.loads(plain[json_start:])
    elif isinstance(data, dict) and data.get("accessToken"):
        tokens = data
    elif isinstance(data, dict) and isinstance(data.get("json"), (dict, str)):
        inner = data["json"]
        tokens = json.loads(inner) if isinstance(inner, str) else inner
    elif isinstance(data, str) and data.lstrip().startswith("{"):
        tokens = json.loads(data)

    if not isinstance(tokens, dict) or not tokens.get("accessToken"):
        keys = sorted(data) if isinstance(data, dict) else type(data).__name__
        raise ValueError(
            f"login returned no tokens: code={code} data={str(data)[:200]!r} keys={keys}"
        )

    tokens.setdefault("uid", "")
    _LOGGER.info("Login successful: uid=%s", tokens.get("uid"))
    return tokens


def run_full_login(
    config: dict[str, str] | None,
    email: str,
    password: str,
    email_code: str,
    *,
    tenant: dict[str, Any] | None = None,
    tenant_cache_path: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the complete login flow: tenant -> state -> code -> validate -> login.

    Returns (tokens, tenant) tuple.
    """
    # Step 1: Get or fetch tenant
    if tenant is None:
        if tenant_cache_path:
            try:
                tenant = load_tenant_cache(tenant_cache_path)
                _LOGGER.debug("Loaded cached tenant from %s", tenant_cache_path)
            except (FileNotFoundError, ValueError, KeyError):
                pass
        if tenant is None:
            tenant = fetch_tenant(config)
            if tenant_cache_path:
                save_tenant_cache(tenant_cache_path, tenant)

    xid = ""

    # Step 2: Get user state
    result = login_get_state(config, tenant, email)
    xid = result.get("xid") or xid

    # Step 3: Validate email code
    result = login_validate_code(
        config, tenant, email, email_code, xid=xid
    )
    xid = result.get("xid") or xid

    # Step 4: Password login
    tokens = login_password(config, tenant, email, password, xid=xid)

    return tokens, tenant


# ============================================================
# Generic authenticated REST calls
# ============================================================

def build_authenticated_headers(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    url: str,
    method: str = "POST",
    body: str = "",
    client_mo_override: str | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Build signed headers for any authenticated XPENG API call.

    Generalized version of build_mqtt_signed_headers that supports
    arbitrary HTTP methods and request bodies.
    """
    # Resolve the device identity FIRST. prepare_request_values() bakes the
    # device id into `rv`, and `rv["deviceId"]` is what goes inside the XTEE
    # authorization - so binding it afterwards leaves a request whose signature
    # says one device and whose headers say another (server: 12010104).
    cfg = _cfg_with_tenant_identity(_get_config(config), tenant)
    rv = prepare_request_values(cfg)
    uid = str(tokens.get("uid") or "")
    if not uid:
        raise ValueError("Login token JSON has no uid")
    raw_access = tokens.get("accessToken") or ""
    raw_refresh = tokens.get("refreshToken") or ""
    if not raw_access or not raw_refresh:
        raise ValueError("Login token JSON has no accessToken/refreshToken")

    key_pool = tenant["_key_pool"]
    # Decrypt tokens to raw bytes (strips nonce prefix like Android client)
    access_plain = decrypt_xtee_payload(raw_access, key_pool)
    refresh_plain = decrypt_xtee_payload(raw_refresh, key_pool)

    headers = base_wire_headers(cfg)
    # Only set Content-Length: 0 for POST/PUT with empty body
    if method.upper() in ("POST", "PUT") and not body:
        headers["Content-Length"] = "0"
    headers.update({
        "Xp-Tee-Id": tenant["teeId"],
        "Xp-Tee-Version": str(tenant.get("version", 2)),
        "Xp-Client-Type": cfg.get("ClientType", "1"),
        "Xp-Appid": cfg.get("AppId", ""),
        "Xp-Brand": cfg.get("Brand", "XPENG"),
        "Xp-Client-Encoding": cfg.get("ClientEncoding", "None"),
        "Xp-Uid": uid,
        "Xp-Client": patch_client_segment(rv["client"], "mo", client_mo_override) if client_mo_override else rv["client"],
        "Xp-Nonce": rv["nonce"],
        "Xp-Encryption-Type": cfg.get("EncryptionType", "2"),
    })

    secret = cfg.get("Secret", APP_SECRET) or APP_SECRET
    inner_canonical = build_canonical(method.upper(), body, headers, url)
    inner_xpsign = hmac_hex(secret, inner_canonical)

    # Auth plaintext as raw bytes with token extension (matches Android client)
    nonce_bytes = str(uuid.uuid4()).encode("utf-8")
    head_data = base64.b64encode(
        compact_json({"xpSign": inner_xpsign}).encode("utf-8")
    )
    extension = (b"&accessToken=" + access_plain
                 + b"&refreshToken=" + refresh_plain)
    auth_plain = nonce_bytes + head_data + extension

    override = cfg.get("LoginKeyIndex", "").strip()
    key_index = int(override) if override else secrets.randbelow(10)
    if key_index not in key_pool:
        raise KeyError(f"key index {key_index} not in tenant key pool")
    headers["Xp-Authorization"] = build_authorization(
        tenant["teeId"],
        rv["deviceId"],
        key_index,
        key_pool[key_index],
        auth_plain,
        version=int(tenant.get("version", 2)),
        nonce_length=len(nonce_bytes),
        timestamp_ms=rv["timestampMs"],
    )
    outer_canonical = build_canonical(method.upper(), body, headers, url)
    signature = hmac_hex(secret, outer_canonical)
    headers["Xp-Signature"] = signature

    return headers, {
        **rv,
        "uid": uid,
        "innerXpSign": inner_xpsign,
        "keyIndex": key_index,
        "canonical": outer_canonical,
        "signature": signature,
    }


def get_authenticated_data(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    path: str,
    method: str = "GET",
    body: str = "",
    base_url: str | None = None,
    client_mo_override: str | None = None,
    public_ca: bool = False,
) -> Any:
    """Make an authenticated REST call to any XPENG endpoint.

    ``public_ca=True`` additionally trusts the system CA store, for endpoints
    that are not signed by the XPENG chain (see make_ssl_context).
    """
    cfg = _get_config(config)
    if base_url is None:
        base_url = cfg.get("LoginBaseUrl", "https://apps-global.xiaopeng.com")
    url = absolute_url(base_url, path)
    headers, _ = build_authenticated_headers(
        config, tenant, tokens, url, method, body,
        client_mo_override=client_mo_override,
    )
    resp = _send_api_request(config, url, method, body, headers, public_ca=public_ca)
    if "error" in resp:
        raise ConnectionError(f"REST call failed: {resp['error']}")
    raw_body = resp.get("body", "")
    _LOGGER.debug("REST %s %s -> status=%s body_len=%d body[:200]=%r",
                  method, path, resp.get("status"), len(raw_body), raw_body[:200])
    if not raw_body or raw_body.lstrip().startswith("<"):
        status = resp.get("status", "?")
        raise ConnectionError(
            f"REST {method} {path} returned non-JSON (status={status}): {raw_body[:120]}"
        )
    body_json = json.loads(raw_body)
    data = body_json.get("data")
    # Handle sData decryption if present
    if isinstance(data, dict) and data.get("sData"):
        key_pool = tenant["_key_pool"]
        plain = decrypt_xtee_blob_flexible(
            str(data["sData"]), key_pool
        ).decode("utf-8", errors="replace")
        json_start = plain.find("{")
        if json_start >= 0:
            data = json.loads(plain[json_start:])
    return data if data is not None else body_json


def refresh_login_tokens(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
) -> dict[str, Any]:
    """Refresh expired access tokens using the refresh token.

    Calls POST /sso/app/v3/token/refresh and returns updated tokens dict.
    The response may contain sData (encrypted) or plain JSON token data.
    """
    cfg = _get_config(config)
    key_pool = tenant["_key_pool"]
    raw_refresh = tokens.get("refreshToken") or ""
    if not raw_refresh:
        raise ValueError("No refreshToken in login tokens")

    refresh_token = normalize_login_token_for_auth(raw_refresh, key_pool)
    path = "/sso/app/v3/token/refresh"
    # Try with body containing refreshToken, then without
    bodies = [
        compact_json({"refreshToken": refresh_token}),
        "",
    ]
    base_url = cfg.get("LoginBaseUrl", "https://apps-global.xiaopeng.com")
    url = absolute_url(base_url, path)
    last_error = None

    for body in bodies:
        try:
            headers, _ = build_authenticated_headers(
                config, tenant, tokens, url, "POST", body
            )
            if not body:
                headers["Content-Length"] = "0"
            resp = _send_api_request(config, url, "POST", body, headers)
            if "error" in resp:
                last_error = resp["error"]
                continue
            body_json = json.loads(resp.get("body", "{}"))
            code = body_json.get("code")
            if code and code != 200:
                # A dead refresh token answers with the same "log in again" code.
                # Raise the typed error immediately and stop: both bodies would
                # fail identically, and the caller needs to distinguish "log in
                # again" from "malformed response" so it can surface reauth
                # instead of retrying this refresh forever.
                if is_token_expired_response(body_json):
                    raise LoginExpiredError(
                        int(code),
                        body_json.get("msg") or body_json.get("desc") or "",
                    )
                last_error = body_json.get("msg") or body_json.get("desc") or f"code={code}"
                continue

            data = body_json.get("data")
            # Handle sData (encrypted response)
            if isinstance(data, dict) and data.get("sData"):
                plain = decrypt_xtee_blob_flexible(
                    str(data["sData"]), key_pool
                ).decode("utf-8", errors="replace")
                json_start = plain.find("{")
                if json_start >= 0:
                    data = json.loads(plain[json_start:])

            if isinstance(data, dict) and data.get("accessToken"):
                # Merge new tokens with existing
                merged = {**tokens, **data}
                merged.setdefault("uid", tokens.get("uid", ""))
                if not merged.get("refreshToken"):
                    merged["refreshToken"] = raw_refresh
                _LOGGER.info("Login tokens refreshed successfully")
                return merged

            # Try the json sub-key
            if isinstance(data, dict) and data.get("json"):
                inner = data["json"]
                if isinstance(inner, str):
                    inner = json.loads(inner)
                if isinstance(inner, dict) and inner.get("accessToken"):
                    merged = {**tokens, **inner}
                    merged.setdefault("uid", tokens.get("uid", ""))
                    if not merged.get("refreshToken"):
                        merged["refreshToken"] = raw_refresh
                    _LOGGER.info("Login tokens refreshed successfully (json key)")
                    return merged

            last_error = f"No accessToken in response: {str(body_json)[:200]}"
        except LoginExpiredError:
            # Must not be swallowed by the broad handler below: a dead refresh
            # token is not a retryable transient.
            raise
        except Exception as err:
            last_error = str(err)
            continue

    raise ConnectionError(f"Token refresh failed: {last_error}")


def get_vehicles(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
) -> Any:
    """Fetch vehicle list from XPENG API."""
    return get_authenticated_data(
        config, tenant, tokens, "/biz/v5/vehicle/v2/myVehicles", "POST",
        compact_json({}),
    )


def get_vehicle_state(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
    vehicle_type_code: str,
) -> Any:
    """Fetch widget vehicle state."""
    body = compact_json({"vin": vin, "vehicleTypeCode": vehicle_type_code})
    return get_authenticated_data(
        config, tenant, tokens, "/biz/v5/widget/vehicle/state", "POST", body
    )


def get_ota_status(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
) -> Any:
    """Fetch OTA/XOS version status."""
    return get_authenticated_data(
        config, tenant, tokens, f"/ota/v3/app/vehicle/status?vin={vin}", "GET"
    )


def get_maintenance_reminder(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
) -> Any:
    """Fetch maintenance reminder data."""
    return get_authenticated_data(
        config, tenant, tokens,
        f"/app/api/carMaintenance/queryReminder/{vin}", "GET",
    )


def get_xcombos(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
    vehicle_type: str = "",
) -> Any:
    """Fetch user's X-Combo (smart scene) list."""
    params: dict[str, Any] = {"vin": vin}
    if vehicle_type:
        params["vehicleType"] = vehicle_type
    body = compact_json(params)
    return get_authenticated_data(
        config, tenant, tokens,
        "/personal-tailor/v3/scene/mine", "POST", body,
    )


def get_xcombo_agg_data(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
    vehicle_type: str = "",
) -> Any:
    """Fetch X-Combo aggregate data (may include more scenes)."""
    params: dict[str, str] = {"vin": vin}
    if vehicle_type:
        params["vehicleType"] = vehicle_type
        params["carType"] = vehicle_type
    body = compact_json(params)
    return get_authenticated_data(
        config, tenant, tokens,
        "/personal-tailor/v3/scene/agg/data", "POST", body,
    )


def get_energy_report(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
    vehicle_type_code: str,
) -> Any:
    """Fetch simple energy consumption report."""
    body = compact_json({
        "vin": vin,
        "vehicleTypeCode": vehicle_type_code,
        "userType": 1,
    })
    return get_authenticated_data(
        config, tenant, tokens,
        "/flow/cv2/app/vehicle/energy/simpleReport", "POST", body,
    )


def get_weather(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    latitude: float,
    longitude: float,
) -> Any:
    """Fetch current weather at the given coordinates (assistant service).

    Endpoint: GET /assistant/assistant/app/weather/eu/coordinates
    Returns a dict with realtimeTemperature, weatherType, realtimeAqiType, etc.
    """
    return get_authenticated_data(
        config, tenant, tokens,
        f"/assistant/assistant/app/weather/eu/coordinates?latitude={latitude}&longitude={longitude}",
        "GET",
    )


def get_remote_capabilities(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
) -> Any:
    """Fetch supported remote commands for the vehicle."""
    body = compact_json({"vin": vin})
    return get_authenticated_data(
        config, tenant, tokens,
        "/biz/v5/vehicle/remoteCapabilities", "POST", body,
    )


def get_charge_state(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
) -> Any:
    """Fetch charge state via REST (supplements MQTT when car is asleep)."""
    return get_authenticated_data(
        config, tenant, tokens,
        f"/biz/v5/vehicles/state/charge?vin={vin}", "GET",
    )


def get_left_comfort_preon_time(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
) -> Any:
    """Fetch seconds remaining for left-comfort preon HVAC mode.

    Backend (LeftComfortModelController) returns a raw Long which is the redis
    TTL on key `left:comfort:model:preon:{vin}`. Conventions: positive int =
    seconds remaining; -2 = key absent (mode not active); -1 = no expiry set.
    """
    return get_authenticated_data(
        config, tenant, tokens,
        f"/biz/v5/left-comfort/preon/time?vin={vin}", "GET",
    )


def clear_vehicle_commands(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
) -> Any:
    """Clear the user's pending vehicle command queue server-side.

    Use when the car returns code:12 ("Vehicle controls in used") to unstick
    the throttle. Endpoint requires Token.uid == body.uid (server-enforced).
    """
    uid = str(tokens.get("uid") or "")
    body = compact_json({"uid": int(uid)} if uid.isdigit() else {"uid": uid})
    return get_authenticated_data(
        config, tenant, tokens,
        "/biz/v5/vehicle/vehicleCmdClear", "POST", body,
    )


def get_hvac_state(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
) -> Any:
    """Fetch HVAC state via REST (supplements MQTT when car is asleep)."""
    return get_authenticated_data(
        config, tenant, tokens,
        f"/biz/v5/vehicles/state/hvac?vin={vin}", "GET",
    )


def get_ota_upgrade_info(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
    upgrade_id: int | None = None,
) -> Any:
    """Fetch detailed OTA upgrade info (available update details)."""
    params = f"?vin={vin}"
    if upgrade_id is not None:
        params += f"&upgradeId={upgrade_id}"
    return get_authenticated_data(
        config, tenant, tokens,
        f"ota/remote/v1/vehicle/getUpgradeInfo{params}", "GET",
    )


def get_ota_version(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
) -> Any:
    """Fetch current OTA/OS version via the remote v1 endpoint."""
    return get_authenticated_data(
        config, tenant, tokens,
        f"ota/remote/v1/vehicle/getOsVersion?vin={vin}", "GET",
    )


def get_auto_upgrade_status(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
) -> Any:
    """Fetch auto-upgrade setting status (0=off, 1=on, 2=dispatching)."""
    return get_authenticated_data(
        config, tenant, tokens,
        f"ota/remote/v1/option/status?vin={vin}", "GET",
    )


def set_auto_upgrade(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
    enabled: bool,
) -> Any:
    """Toggle auto-upgrade setting on or off."""
    body = compact_json({"vin": vin, "autoUpgrade": 1 if enabled else 0})
    return get_authenticated_data(
        config, tenant, tokens,
        "ota/remote/v1/option/change", "POST", body,
    )


def trigger_ota_upgrade(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
    upgrade_id: int,
    error_code: str | None = None,
) -> Any:
    """Initiate an OTA upgrade on the vehicle."""
    payload: dict[str, Any] = {"vin": vin, "upgradeId": upgrade_id}
    if error_code:
        payload["errorCode"] = error_code
    body = compact_json(payload)
    return get_authenticated_data(
        config, tenant, tokens,
        "ota/remote/v1/vehicle/upgrade", "POST", body,
    )


def cancel_ota_schedule(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
    upgrade_id: int,
) -> Any:
    """Cancel a scheduled OTA upgrade."""
    body = compact_json({"vin": vin, "upgradeId": upgrade_id})
    return get_authenticated_data(
        config, tenant, tokens,
        "ota/remote/v1/vehicle/cancelSchedule", "POST", body,
    )


def send_ota_feedback(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
    upgrade_id: int,
    status: int,
) -> Any:
    """Send OTA upgrade status feedback to the server."""
    body = compact_json({"vin": vin, "upgradeId": upgrade_id, "status": status})
    return get_authenticated_data(
        config, tenant, tokens,
        "ota/remote/v1/vehicle/status/feedback", "POST", body,
    )


def get_beta_popup(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
) -> Any:
    """Fetch beta program popup data (beta test availability)."""
    return get_authenticated_data(
        config, tenant, tokens,
        "/app/api/profile/betaPopup/get", "POST", compact_json({}),
    )


def get_xpilot_authcodes(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
) -> Any:
    """Fetch XPilot activation auth codes for the vehicle."""
    return get_authenticated_data(
        config, tenant, tokens,
        f"xpilot-activate/v2/app/authcode/list?vin={vin}", "GET",
    )


def get_parking_info(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
) -> Any:
    """Fetch last parking location with photo/video."""
    return get_authenticated_data(
        config, tenant, tokens,
        f"/zhitu/v1/app/parking/info?vin={vin}", "GET",
    )


def get_vehicle_extension(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
) -> Any:
    """Fetch vehicle extension messages (alerts, notifications)."""
    return get_authenticated_data(
        config, tenant, tokens,
        f"/biz/v5/vehicle/extension/message?vin={vin}", "GET",
    )


MSGCENTER_BASE_URL = "https://msgcenter-oversea-eur.xiaopeng.com"


def get_message_categories(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
) -> Any:
    """List message-center categories with unread counts."""
    return get_authenticated_data(
        config, tenant, tokens,
        "/biz/v5/message/category", "POST", "{}",
        base_url=MSGCENTER_BASE_URL,
    )


def get_messages_by_category(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    category_id: Any,
    page_num: int = 1,
    page_size: int = 20,
) -> Any:
    """List messages within a given category."""
    body = compact_json({
        "categoryId": category_id,
        "pageNum": page_num,
        "pageSize": page_size,
    })
    return get_authenticated_data(
        config, tenant, tokens,
        "/biz/v5/message/listByCategory", "POST", body,
        base_url=MSGCENTER_BASE_URL,
    )


#: Message-centre classification codes. Discovered from the app binary, which
#: contains the literal set; the message centre lists one category at a time and
#: returns items only for the category that actually holds them.
#: XPMOBILE-VEH is the "Vehicle Notification" list - where X-Combos, sentry and
#: charge notifications land.
CLASSIFICATION_VEHICLE: Final = "XPMOBILE-VEH"
NOTIFICATION_CLASSIFICATIONS: Final = (
    CLASSIFICATION_VEHICLE,   # Vehicle Notification
    "XPMOBILE-PUS",           # Push / system
    "XPMOBILE-CHA",           # Charging
    "XPMOBILE-SER",           # Service
    "XPMOBILE-ORD",           # Orders
    "XPMOBILE-JOY",           # Journeys
)


def get_notifications(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    page_num: int = 1,
    page_size: int = 30,
    classification: str = CLASSIFICATION_VEHICLE,
) -> Any:
    """Fetch one page of message-centre notifications.

    The request shape is not obvious and getting it wrong produces a misleading
    error rather than a validation message. Verified against the real service:

        {"pageNum":1,"pageSize":20}
            -> {"code":400,"msg":"Server unavailable. Please try again later"}

        {"param":{"firstClassificationCode":"XPMOBILE-VEH"},
         "size":30,"uid":"<uid>","page":1}
            -> [ ...items... ]   (or [] when the category is empty)

    ``param.firstClassificationCode`` is required but its VALUE is not validated:
    any non-empty string is accepted, and asking for a category with no messages
    simply returns ``[]``. That is why a wrong-but-present code looks like "no
    notifications" instead of an error - the original code here sent no ``param``
    at all, so it always saw the 400 and looked like a server outage.
    """
    body = compact_json({
        "param": {"firstClassificationCode": classification},
        "size": page_size,
        "uid": str(tokens.get("uid") or ""),
        "page": page_num,
    })
    # public_ca: the message centre is fronted by Aliyun's WAF and presents a
    # public certificate, unlike apps-global which is signed by the XPENG chain.
    return get_authenticated_data(
        config, tenant, tokens,
        "/v3/app/notification/page", "POST", body,
        base_url=MSGCENTER_BASE_URL,
        public_ca=True,
    )


def parse_notification_item(item: dict[str, Any]) -> dict[str, Any]:
    """Flatten one message-centre item into the shape the coordinator records.

    Raw item::

        {"id": "...", "messageId": "...", "read": false,
         "createTime": "2026-10-09 09:44:17.0000",
         "messageContent": "{\"msgContent\":{\"title\":\"XCOMBO-...\",
                            \"description\":\"<p>...</p>\"},...}"}

    ``messageContent`` is a JSON *string*, only sometimes valid, and the
    description is HTML - both are handled here so callers get plain text.
    """
    _LOGGER.debug("Message-centre item raw: %s", item)

    import re as _re

    out: dict[str, Any] = {
        "id": str(item.get("id") or ""),
        "msg_id": str(item.get("messageId") or item.get("id") or ""),
        "read": bool(item.get("read")),
        "timestamp_raw": item.get("createTime") or "",
        "source": "rest",
        # The record before any parsing, so an XPENG field rename can be fixed
        # from a captured event or sensor attribute instead of a live probe.
        # `messageContent` stays the JSON *string* here, not the parsed dict.
        "raw": dict(item),
    }

    # createTime is "YYYY-MM-DD HH:MM:SS.0000" - space separator, 4-digit
    # fraction. fromisoformat rejects the 4-digit part, so normalise it.
    raw_ts = str(item.get("createTime") or "")
    if raw_ts:
        try:
            from datetime import datetime as _dt
            stamp = raw_ts.replace(" ", "T", 1)
            head, _, frac = stamp.partition(".")
            frac = (frac + "000000")[:6]
            out["timestamp"] = _dt.fromisoformat(f"{head}.{frac}").isoformat()
        except Exception:
            out["timestamp"] = raw_ts

    content = item.get("messageContent")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            content = {"desc": content}
    if not isinstance(content, dict):
        content = {}

    inner = content.get("msgContent")
    if not isinstance(inner, dict):
        inner = {}

    def _plain(value: Any) -> str:
        text = str(value or "")
        text = _re.sub(r"<br\s*/?>", "\n", text, flags=_re.I)
        text = _re.sub(r"<[^>]+>", "", text)
        return text.strip()

    out["title"] = _plain(inner.get("title") or content.get("title"))
    out["message"] = _plain(
        inner.get("description") or content.get("desc") or content.get("content")
    )
    out["category"] = content.get("category")
    out["msg_type"] = content.get("msgType")
    if inner.get("link"):
        out["link"] = inner["link"]
    return out


def get_aws_sts_token(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
) -> Any:
    """Get temporary AWS STS credentials for XPENG S3/OSS access."""
    return get_authenticated_data(
        config, tenant, tokens,
        "/biz/v5/aws/token", "POST", "",
    )


def get_aliyun_sts_token(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
) -> Any:
    """Get temporary Aliyun STS credentials for XPENG OSS access."""
    return get_authenticated_data(
        config, tenant, tokens,
        "/biz/v5/aliyun/token", "POST", "",
    )


def control_vehicle_rest(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
    command: dict[str, Any],
) -> Any:
    """Send vehicle control command via REST (digital-key fallback)."""
    body = compact_json({"vin": vin, **command})
    return get_authenticated_data(
        config, tenant, tokens,
        "/digital-key/v1/key/vehicle/controlVehicle", "POST", body,
    )


def execute_xcombo_scene(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    scene_id: str,
    vin: str,
) -> Any:
    """Execute (one-key start) an X-Combo scene."""
    body = compact_json({"sceneId": scene_id, "vin": vin})
    return get_authenticated_data(
        config, tenant, tokens,
        "/personal-tailor/v3/scene/lifecycle", "POST", body,
    )


def toggle_xcombo_scene(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    scene_id: str,
    vin: str,
    active: bool,
) -> Any:
    """Toggle an X-Combo scene active/inactive."""
    body = compact_json({
        "sceneId": scene_id,
        "vin": vin,
        "active": 1 if active else 0,
    })
    return get_authenticated_data(
        config, tenant, tokens,
        "/personal-tailor/v3/scene/active", "PUT", body,
    )




def share_xcombo_scene(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    scene_id: str,
    vin: str = "",
) -> Any:
    """Issue a share code for an X-Combo scene.

    Returns the endpoint payload, which for the app carries ``sceneCode`` (the
    8-digit code shown as a QR/link) and ``shareUrl``.
    """
    params: dict[str, Any] = {"sceneId": scene_id}
    if vin:
        params["vin"] = vin
    body = compact_json(params)
    return get_authenticated_data(
        config, tenant, tokens,
        "/personal-tailor/v3/scene/share", "POST", body,
    )


def inherit_xcombo_scene(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    scene_code: str,
    vin: str = "",
) -> Any:
    """Import an X-Combo scene from an 8-digit share code.

    This is the "copy" direction: it adds the shared combo to the account.
    ``scene_code`` is normalised to a string so a leading zero survives
    (codes such as ``04430024`` appear in the wild).
    """
    params: dict[str, Any] = {"sceneCode": str(scene_code)}
    if vin:
        params["vin"] = vin
    body = compact_json(params)
    return get_authenticated_data(
        config, tenant, tokens,
        "/personal-tailor/v3/scene/inheritScene", "POST", body,
    )


def get_xcombo_share_info(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    scene_id: str,
    vin: str = "",
) -> Any:
    """Read a scene's share information without issuing a new code."""
    params: dict[str, Any] = {"sceneId": scene_id}
    if vin:
        params["vin"] = vin
    body = compact_json(params)
    return get_authenticated_data(
        config, tenant, tokens,
        "/personal-tailor/v3/scene/detail", "POST", body,
    )


def save_xcombo_scene(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    scene: dict[str, Any],
) -> Any:
    """Create or modify an X-Combo scene (the authoring call).

    ``scene`` is passed through verbatim. The server's expected body is NOT yet
    confirmed, and guessing it against a live vehicle API is not acceptable, so
    this function deliberately adds nothing. Populate ``scene`` from a captured
    ``scene/update`` request before using it.
    """
    return get_authenticated_data(
        config, tenant, tokens,
        "/personal-tailor/v3/scene/update", "POST", compact_json(dict(scene)),
    )
def create_aircondition_appointment(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
    action_type: str,
    start_time: str,
    cycle_type: int,
    schedule: int,
    is_valid: int,
    time_zone: str | None = None,
    source_type: int = 1,
) -> Any:
    """Create an HVAC scheduled appointment for the vehicle."""
    uid_raw = str(tokens.get("uid") or "")
    item: dict[str, Any] = {
        "actionType": action_type,
        "startTime": start_time,
        "cycleType": cycle_type,
        "schedule": schedule,
        "isValid": is_valid,
        "sourceType": source_type,
    }
    payload: dict[str, Any] = {"vin": vin, "list": [item]}
    if uid_raw.isdigit():
        payload["uid"] = int(uid_raw)
    if time_zone:
        payload["timeZone"] = time_zone
    body = compact_json(payload)
    return get_authenticated_data(
        config, tenant, tokens,
        "/biz/v5/appointments/aircondition/create", "POST", body,
    )


def update_aircondition_appointment(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
    appointment_id: str,
    action_type: str,
    start_time: str,
    cycle_type: int,
    schedule: int,
    is_valid: int,
    time_zone: str | None = None,
    op_type: str = "UPDATE",
) -> Any:
    """Update an existing HVAC scheduled appointment."""
    uid_raw = str(tokens.get("uid") or "")
    item: dict[str, Any] = {
        "appointmentId": appointment_id,
        "actionType": action_type,
        "startTime": start_time,
        "cycleType": cycle_type,
        "schedule": schedule,
        "isValid": is_valid,
        "opType": op_type,
    }
    payload: dict[str, Any] = {"vin": vin, "list": [item]}
    if uid_raw.isdigit():
        payload["uid"] = int(uid_raw)
    if time_zone:
        payload["timeZone"] = time_zone
    body = compact_json(payload)
    return get_authenticated_data(
        config, tenant, tokens,
        "/biz/v5/appointments/aircondition/update", "POST", body,
    )


def delete_aircondition_appointment(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
    appointment_id: str,
    time_zone: str | None = None,
) -> Any:
    """Delete an HVAC scheduled appointment by id."""
    payload: dict[str, Any] = {
        "vin": vin,
        "appointmentIdList": [appointment_id],
    }
    if time_zone:
        payload["timeZone"] = time_zone
    body = compact_json(payload)
    return get_authenticated_data(
        config, tenant, tokens,
        "/biz/v5/appointments/aircondition/delete", "POST", body,
    )


def list_aircondition_appointments(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
    action: str = "AIR_CONDITION",
    time_zone: str | None = None,
) -> Any:
    """List HVAC scheduled appointments for the vehicle."""
    uid_raw = str(tokens.get("uid") or "")
    payload: dict[str, Any] = {"vin": vin, "action": action}
    if uid_raw.isdigit():
        payload["uid"] = int(uid_raw)
    if time_zone:
        payload["timeZone"] = time_zone
    body = compact_json(payload)
    return get_authenticated_data(
        config, tenant, tokens,
        "/biz/v5/appointments/aircondition/list", "POST", body,
    )


def send_to_vehicle(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
    name: str,
    latitude: float,
    longitude: float,
    label: str | None = None,
    poi_id: str | None = None,
    msg_type: str | None = None,
) -> Any:
    """Send a navigation destination card to the vehicle head unit.

    Backs the official app's "send POI to car" feature via the
    user-authenticated REST channel (POST /biz/v5/app/sendToVehicle). The
    cloud service formats the destination into a head-unit navigation card
    and forwards it to the car over MQTT.
    """
    payload: dict[str, Any] = {
        "vin": vin,
        "name": name,
        "lat": latitude,
        "lon": longitude,
    }
    if label:
        payload["label"] = label
    if poi_id:
        payload["poiId"] = poi_id
    if msg_type:
        payload["msgType"] = msg_type
    body = compact_json(payload)
    return get_authenticated_data(
        config, tenant, tokens,
        "/biz/v5/app/sendToVehicle", "POST", body,
    )


def report_schedule_sync_status(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
    status: int = 1,
) -> Any:
    """Enable/disable phone-calendar schedule sync (AppScheduleEuController).

    Endpoint: POST /assistant/v1/eu/app/sync/schedules/user/auth/status
    `status` is a switch: 1 = enabled, 0 = disabled. Re-asserting status=1 is
    the same trigger as the head-unit "sync" button.
    """
    body = compact_json({"status": status, "vin": vin})
    return get_authenticated_data(
        config, tenant, tokens,
        "/assistant/v1/eu/app/sync/schedules/user/auth/status", "POST", body,
    )


def get_schedule_sync_metadata(
    config: dict[str, str] | None,
    tenant: dict[str, Any],
    tokens: dict[str, Any],
    vin: str,
) -> Any:
    """Fetch schedule-sync metadata (userAuthStatus + schedule data md5s)."""
    return get_authenticated_data(
        config, tenant, tokens,
        f"/assistant/v1/eu/app/sync/schedules/metadata?vin={vin}", "GET",
    )