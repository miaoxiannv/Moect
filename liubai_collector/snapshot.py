# -*- coding: utf-8 -*-
"""把加密库解密成本地快照，并支持增量同步。

同步策略（从便宜到贵，命中即停）：
1. 主库与 WAL 都没变 -> 直接跳过（毫秒级）
2. 主库没变、只有 WAL 变 -> 只把 WAL 新帧合并进已有快照
3. 主库只在尾部追加了页 -> 只解密新增页
4. 其它情况（页被覆写、文件变小）-> 全量重解

指纹用 (size, mtime_ns) 而不是哈希：1GB 数据做哈希比解密还慢。
"""
from __future__ import annotations

import json
import os

import cipher

DEFAULT_INCLUDE = (
    "session/session.db",
    "contact/contact.db",
    "message/message_0.db",
    "message/message_1.db",
    "message/message_2.db",
    "message/message_3.db",
)

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1


def _rel(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _fingerprint(path: str) -> dict | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    wal = path + "-wal"
    try:
        wst = os.stat(wal)
        wal_size, wal_mtime = wst.st_size, wst.st_mtime_ns
    except OSError:
        wal_size, wal_mtime = 0, 0
    return {
        "size": st.st_size,
        "mtime": st.st_mtime_ns,
        "walSize": wal_size,
        "walMtime": wal_mtime,
    }


def load_manifest(out_dir: str) -> dict:
    path = os.path.join(out_dir, MANIFEST_NAME)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") == MANIFEST_VERSION:
            data.setdefault("databases", {})
            return data
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return {"version": MANIFEST_VERSION, "databases": {}}


def save_manifest(out_dir: str, manifest: dict) -> None:
    path = os.path.join(out_dir, MANIFEST_NAME)
    try:
        os.makedirs(out_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError:
        pass


def _decrypt_tail(src: str, dst: str, enc_key: bytes, start_page: int) -> int:
    """从 start_page 开始解密并追加到已有文件，返回写入页数。"""
    with open(src, "rb") as fi:
        fi.seek((start_page - 1) * cipher.PAGE_SZ)
        page_no = start_page
        with open(dst, "ab") as fo:
            while True:
                page = fi.read(cipher.PAGE_SZ)
                if not page:
                    break
                fo.write(cipher.decrypt_page(enc_key, page, page_no))
                page_no += 1
    return page_no - start_page


def sync_one(src: str, dst: str, db_storage: str, out_dir: str,
             keys: dict, manifest: dict, rel: str) -> dict:
    """同步单个库，返回本次动作与统计。"""
    fp = _fingerprint(src)
    if fp is None:
        return {"rel": rel, "action": "missing", "ok": False, "error": "文件不存在"}

    with open(src, "rb") as f:
        page1 = f.read(cipher.PAGE_SZ)
    if len(page1) < cipher.PAGE_SZ:
        return {"rel": rel, "action": "small", "ok": False, "error": "文件过小"}
    salt = page1[:cipher.SALT_SZ].hex()
    key_hex = keys.get(salt)
    if not key_hex:
        return {"rel": rel, "action": "nokey", "ok": False, "error": "缺少该库的密钥"}
    enc_key = bytes.fromhex(key_hex)

    prev = manifest["databases"].get(rel)
    dst_exists = os.path.exists(dst)

    # 1) 完全没变
    if (prev and dst_exists
            and prev.get("size") == fp["size"] and prev.get("mtime") == fp["mtime"]
            and prev.get("walSize") == fp["walSize"] and prev.get("walMtime") == fp["walMtime"]):
        return {"rel": rel, "action": "skip", "ok": True, "pages": prev.get("pages", 0)}

    # 2) 主库没变，只有 WAL 有新帧
    if (prev and dst_exists
            and prev.get("size") == fp["size"] and prev.get("mtime") == fp["mtime"]):
        wal = src + "-wal"
        applied = 0
        if fp["walSize"] > cipher.WAL_HDR_SZ and os.path.exists(wal):
            try:
                applied = cipher.merge_wal(dst, wal, enc_key)
            except Exception as exc:  # noqa: BLE001
                return {"rel": rel, "action": "wal-failed", "ok": False, "error": str(exc)}
        manifest["databases"][rel] = {**fp, "pages": prev.get("pages", 0)}
        return {"rel": rel, "action": "wal", "ok": True, "walFrames": applied,
                "pages": prev.get("pages", 0)}

    # 3) 只在尾部追加了页
    if (prev and dst_exists and fp["size"] > prev.get("size", 0)
            and prev.get("size", 0) % cipher.PAGE_SZ == 0):
        start = prev["size"] // cipher.PAGE_SZ + 1
        # 已解密文件按已解密的页数对齐，避免把 WAL 曾经写过的页重复追加
        try:
            written = _decrypt_tail(src, dst, enc_key, start)
            pages = max(prev.get("pages", 0), start - 1 + written)
            manifest["databases"][rel] = {**fp, "pages": pages}
            return {"rel": rel, "action": "append", "ok": True, "pages": pages, "newPages": written}
        except Exception as exc:  # noqa: BLE001
            return {"rel": rel, "action": "append-failed", "ok": False, "error": str(exc)}

    # 4) 兜底：全量重解
    try:
        info = cipher.decrypt_file(src, dst, enc_key)
        manifest["databases"][rel] = {**fp, "pages": info["pages"]}
        return {"rel": rel, "action": "full", "ok": True, "pages": info["pages"],
                "walFrames": info["wal_frames"]}
    except Exception as exc:  # noqa: BLE001
        return {"rel": rel, "action": "full-failed", "ok": False, "error": str(exc)}


def sync_databases(db_storage: str, out_dir: str, keys: dict,
                   include=None, progress=None) -> dict:
    """增量同步指定库；返回逐库动作明细与汇总。"""
    progress = progress or (lambda msg: None)
    wanted = list(include or DEFAULT_INCLUDE)
    manifest = load_manifest(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    details = []
    for rel in wanted:
        src = os.path.join(db_storage, rel.replace("/", os.sep))
        dst = os.path.join(out_dir, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        result = sync_one(src, dst, db_storage, out_dir, keys, manifest, rel)
        details.append(result)
        if result["action"] not in ("skip",):
            progress("%s：%s" % (rel, result["action"]))

    save_manifest(out_dir, manifest)
    summary = {
        "total": len(details),
        "skipped": sum(1 for d in details if d["action"] == "skip"),
        "incremental": sum(1 for d in details if d["action"] in ("wal", "append")),
        "full": sum(1 for d in details if d["action"] == "full"),
        "failed": sum(1 for d in details if not d.get("ok")),
        "details": details,
    }
    return summary
