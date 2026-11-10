# -*- coding: utf-8 -*-
"""用 Windows DPAPI 保护数据库密钥缓存。

密钥只属于当前 Windows 用户：换用户、换机器都解不开。
这样重连可以跳过 40 秒的内存扫描，又不必把明文密钥写到磁盘。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os

crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[DATA_BLOB, ctypes.Array]:
    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def protect(data: bytes) -> bytes | None:
    src, _keep = _blob(data)
    dst = DATA_BLOB()
    ok = crypt32.CryptProtectData(
        ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst)
    )
    if not ok:
        return None
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        kernel32.LocalFree(dst.pbData)


def unprotect(data: bytes) -> bytes | None:
    src, _keep = _blob(data)
    dst = DATA_BLOB()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst)
    )
    if not ok:
        return None
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        kernel32.LocalFree(dst.pbData)


def save_keys(out_dir: str, keys: dict, fingerprint: str) -> bool:
    """fingerprint 用来判断缓存是否属于同一个账号的同一批库。"""
    try:
        os.makedirs(out_dir, exist_ok=True)
        payload = json.dumps({"fingerprint": fingerprint, "keys": keys}).encode("utf-8")
        sealed = protect(payload)
        if sealed is None:
            return False
        with open(os.path.join(out_dir, "keys.bin"), "wb") as f:
            f.write(sealed)
        return True
    except OSError:
        return False


def load_keys(out_dir: str, fingerprint: str) -> dict | None:
    path = os.path.join(out_dir, "keys.bin")
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    plain = unprotect(raw)
    if plain is None:
        return None
    try:
        data = json.loads(plain.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if data.get("fingerprint") != fingerprint:
        return None
    keys = data.get("keys")
    return keys if isinstance(keys, dict) and keys else None


def account_fingerprint(db_storage: str) -> str:
    """账号目录 + 库文件数量，足够区分账号，也不随消息变化。"""
    try:
        count = sum(1 for _r, _d, fs in os.walk(db_storage) for f in fs if f.endswith(".db"))
    except OSError:
        count = 0
    return "%s|%d" % (os.path.basename(os.path.dirname(db_storage)), count)
