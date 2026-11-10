# -*- coding: utf-8 -*-
"""微信 4.x 的 SQLCipher 4 参数与页级解密。

页大小 4096，KDF 为 PBKDF2-HMAC-SHA512 256000 轮输出 32 字节，
每页保留区 80 字节 = IV(16) + HMAC(64)，第 1 页前 16 字节是 salt。
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import os
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
IV_SZ = 16
HMAC_SZ = 64
RESERVE_SZ = 80
KDF_ITER = 256000
SQLITE_HDR = b"SQLite format 3" + bytes(1)


def derive_enc_key(passphrase: bytes, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha512", passphrase, salt, KDF_ITER, dklen=KEY_SZ)


def hmac_for_page1(enc_key: bytes, page1: bytes) -> bytes:
    salt = page1[:SALT_SZ]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)
    mac = hmac_mod.new(mac_key, page1[SALT_SZ:PAGE_SZ - RESERVE_SZ + IV_SZ], hashlib.sha512)
    mac.update(struct.pack("<I", 1))
    return mac.digest()


def verify_enc_key(enc_key: bytes, page1: bytes) -> bool:
    if len(page1) < PAGE_SZ:
        return False
    return hmac_for_page1(enc_key, page1) == page1[PAGE_SZ - HMAC_SZ:PAGE_SZ]


def _aes_cbc_decrypt(key: bytes, iv: bytes, body: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(body) + decryptor.finalize()


def decrypt_page(enc_key: bytes, page: bytes, pgno: int) -> bytes:
    if len(page) < PAGE_SZ:
        page = page + bytes(PAGE_SZ - len(page))
    iv = page[PAGE_SZ - RESERVE_SZ:PAGE_SZ - RESERVE_SZ + IV_SZ]
    if pgno == 1:
        body = page[SALT_SZ:PAGE_SZ - RESERVE_SZ]
        return SQLITE_HDR + _aes_cbc_decrypt(enc_key, iv, body) + bytes(RESERVE_SZ)
    body = page[:PAGE_SZ - RESERVE_SZ]
    return _aes_cbc_decrypt(enc_key, iv, body) + bytes(RESERVE_SZ)


WAL_MAGIC = {0x377F0682, 0x377F0683}
WAL_HDR_SZ = 32
WAL_FRAME_HDR_SZ = 24


def merge_wal(db_path: str, wal_path: str, enc_key: bytes) -> int:
    """把 WAL 里的增量页解密后写回已解密的库，只应用最后一个 commit 之前的帧。"""
    with open(wal_path, "rb") as wal:
        header = wal.read(WAL_HDR_SZ)
        if len(header) < WAL_HDR_SZ:
            return 0
        magic, _ver, page_size = struct.unpack_from(">III", header, 0)
        if magic not in WAL_MAGIC:
            return 0
        if page_size not in (0, PAGE_SZ):
            return 0
        salts = struct.unpack_from(">II", header, 16)
        size = os.fstat(wal.fileno()).st_size
        last_commit = 0
        committed_pages = 0
        while wal.tell() + WAL_FRAME_HDR_SZ + PAGE_SZ <= size:
            frame = wal.read(WAL_FRAME_HDR_SZ)
            pgno, commit = struct.unpack_from(">II", frame, 0)
            wal.seek(PAGE_SZ, os.SEEK_CUR)
            if not pgno:
                break
            if struct.unpack_from(">II", frame, 8) == salts and commit:
                last_commit, committed_pages = wal.tell(), commit
        if not last_commit:
            return 0
        wal.seek(WAL_HDR_SZ)
        applied = 0
        with open(db_path, "r+b") as database:
            while wal.tell() < last_commit:
                frame = wal.read(WAL_FRAME_HDR_SZ)
                page = wal.read(PAGE_SZ)
                if len(frame) != WAL_FRAME_HDR_SZ or len(page) != PAGE_SZ:
                    break
                pgno = struct.unpack_from(">I", frame, 0)[0]
                if struct.unpack_from(">II", frame, 8) != salts or pgno > committed_pages:
                    continue
                database.seek((pgno - 1) * PAGE_SZ)
                database.write(decrypt_page(enc_key, page, pgno))
                applied += 1
            database.truncate(committed_pages * PAGE_SZ)
        return applied


def decrypt_file(src: str, dst: str, enc_key: bytes, with_wal: bool = True) -> dict:
    size = os.path.getsize(src)
    total = (size + PAGE_SZ - 1) // PAGE_SZ
    with open(src, "rb") as f:
        page1 = f.read(PAGE_SZ)
    if len(page1) < PAGE_SZ:
        raise ValueError("文件过小：" + src)
    if not verify_enc_key(enc_key, page1):
        raise ValueError("密钥校验失败：" + src)

    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        for pgno in range(1, total + 1):
            page = fi.read(PAGE_SZ)
            if not page:
                break
            fo.write(decrypt_page(enc_key, page, pgno))

    info = {"src": src, "dst": dst, "pages": total, "wal_frames": 0}
    wal = src + "-wal"
    if with_wal and os.path.exists(wal) and os.path.getsize(wal) > WAL_HDR_SZ:
        info["wal_frames"] = merge_wal(dst, wal, enc_key)
    return info
