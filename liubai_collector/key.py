# -*- coding: utf-8 -*-
"""从运行中的 Weixin.exe 提取数据库密钥。

微信 4.1+ 不再把可用的 raw key 明文放在内存里，而是保留一个
`com.Tencent.WCDB.Config.Cipher` 配置对象，对象内的字符串是
用固定掩码异或过的 x'<key><salt>'。步骤：

1. 全内存找 needle 字符串地址
2. 找引用它的 {指针, 长度} 对，反推 std::string 节点
3. 从节点取配置对象指针，读 +0x88 处的 blob
4. 按固定掩码异或还原，正则取出 x'...' 字面量
5. 用数据库第一页的 HMAC-SHA512 校验，通过才算数
"""
from __future__ import annotations

import re
import struct
import time

import winmem
from cipher import SALT_SZ, KEY_SZ, derive_enc_key, verify_enc_key

CONFIG_CIPHER_NAME = b"com.Tencent.WCDB.Config.Cipher"
CONFIG_XOR_MASK = bytes.fromhex(
    "d2c7442458020000004889442450488b"
    "450048844c2448488944254048584c24"
)
CONFIG_BLOB_MAX = 1024
LITERAL_RE = re.compile(rb"[xX]'([0-9a-fA-F]{64,192})'")


def _xor(data: bytes, mask: bytes) -> bytes:
    return bytes(v ^ mask[i % len(mask)] for i, v in enumerate(data))


def _looks_like_key(k: bytes) -> bool:
    return len(k) == KEY_SZ and len(set(k)) >= 15


def _literal_candidates(run: str):
    """一段 64~192 位 hex 里滑窗切出 (key, salt) 候选。"""
    out = []
    starts = [0]
    if len(run) > 96:
        starts.extend(range(0, len(run) - 63, 32))
        starts.append(len(run) - 64)
    for st in dict.fromkeys(starts):
        if st < 0 or st + 64 > len(run):
            continue
        try:
            if not _looks_like_key(bytes.fromhex(run[st:st + 64])):
                continue
        except ValueError:
            continue
        salt = run[st + 64:st + 96] if st + 96 <= len(run) else None
        out.append((run[st:st + 64], salt))
    return out


def scan_process(pid: int, salt_index: dict, found: dict, verbose: bool = False,
                 passphrase_budget: list[int] | None = None) -> dict:
    """在单个进程里找 Config.Cipher，命中后把 (salt -> enc_key) 写进 found。"""
    stats = {"pid": pid, "needle": 0, "refs": 0, "nodes": 0, "blobs": 0,
             "candidates": 0, "hit": 0, "readable_mb": 0}
    handle = winmem.open_process(pid)
    if not handle:
        stats["error"] = "OpenProcess 失败（错误码 %d）" % __import__("ctypes").get_last_error()
        return stats
    try:
        regions = winmem.readable_regions(handle)
        stats["readable_mb"] = sum(s for _b, s in regions) // (1024 * 1024)

        needle_addrs = set()
        for base, data in winmem.iter_chunks(handle, regions, overlap=len(CONFIG_CIPHER_NAME) - 1):
            pos = data.find(CONFIG_CIPHER_NAME)
            while pos >= 0:
                needle_addrs.add(base + pos)
                pos = data.find(CONFIG_CIPHER_NAME, pos + 1)
        stats["needle"] = len(needle_addrs)
        if not needle_addrs:
            return stats

        pairs = [
            struct.pack("<Q", a) + struct.pack("<Q", len(CONFIG_CIPHER_NAME))
            for a in needle_addrs
        ]
        for base, data in winmem.iter_chunks(handle, regions, overlap=0x80):
            if len(found) == len(salt_index):
                break
            for pat in pairs:
                pos = data.find(pat)
                while pos >= 0:
                    stats["refs"] += 1
                    node = winmem.read_memory(handle, base + pos - 0x10, 0x50)
                    if node and len(node) >= 0x40:
                        if (winmem.u64(node, 0x10) in needle_addrs
                                and winmem.u64(node, 0x18) == len(CONFIG_CIPHER_NAME)):
                            cfg = winmem.u64(node, 0x28)
                            if 0x10000 <= cfg < winmem.MAX_USER_ADDRESS:
                                stats["nodes"] += 1
                                obj = winmem.read_memory(handle, cfg + 0x88, 0x28)
                                if obj and len(obj) >= 0x18:
                                    dptr = winmem.u64(obj, 0x8)
                                    dlen = winmem.u64(obj, 0x10)
                                    if 0 < dlen <= CONFIG_BLOB_MAX and 0x10000 <= dptr < winmem.MAX_USER_ADDRESS:
                                        blob = winmem.read_memory(handle, dptr, int(dlen))
                                        if blob and len(blob) == dlen:
                                            stats["blobs"] += 1
                                            decoded = _xor(blob, CONFIG_XOR_MASK)
                                            for m in LITERAL_RE.finditer(decoded):
                                                run = m.group(1).decode("ascii").lower()
                                                for key_hex, salt_hint in _literal_candidates(run):
                                                    stats["candidates"] += 1
                                                    key = bytes.fromhex(key_hex)
                                                    targets = (
                                                        [salt_hint] if salt_hint in salt_index and salt_hint not in found
                                                        else [s for s in salt_index if s not in found]
                                                    )
                                                    for salt in targets:
                                                        page1 = salt_index[salt]
                                                        candidate = key
                                                        if passphrase_budget is not None:
                                                            if passphrase_budget[0] <= 0:
                                                                break
                                                            passphrase_budget[0] -= 1
                                                            candidate = derive_enc_key(key, bytes.fromhex(salt))
                                                        if verify_enc_key(candidate, page1):
                                                            found[salt] = candidate.hex()
                                                            stats["hit"] += 1
                    pos = data.find(pat, pos + 1)
    finally:
        winmem.kernel32.CloseHandle(handle)
    return stats


def collect_salts(db_storage: str) -> dict:
    """扫描目录下所有 .db，记录 salt -> 第一页，用于校验候选密钥。"""
    import os
    index: dict[str, bytes] = {}
    for root, _dirs, files in os.walk(db_storage):
        for name in files:
            if not name.endswith(".db"):
                continue
            path = os.path.join(root, name)
            try:
                if os.path.getsize(path) < 4096:
                    continue
                with open(path, "rb") as f:
                    page1 = f.read(4096)
            except OSError:
                continue
            if len(page1) < 4096:
                continue
            index.setdefault(page1[:SALT_SZ].hex(), page1)
    return index


def scan_legacy(pid: int, salt_index: dict, found: dict) -> int:
    """Try older WeChat versions that retain key/salt literals in memory."""
    handle = winmem.open_process(pid)
    if not handle:
        return 0
    hits = 0
    try:
        for _base, data in winmem.iter_chunks(handle, winmem.readable_regions(handle), overlap=200):
            for match in LITERAL_RE.finditer(data):
                run = match.group(1).decode("ascii").lower()
                if len(run) < 96:
                    continue
                for key_hex, salt in _literal_candidates(run):
                    if salt in salt_index and salt not in found and verify_enc_key(bytes.fromhex(key_hex), salt_index[salt]):
                        found[salt] = key_hex
                        hits += 1
            if len(found) == len(salt_index):
                break
    finally:
        winmem.kernel32.CloseHandle(handle)
    return hits


def extract(db_storage: str, verbose: bool = True, diagnostics: dict | None = None) -> dict:
    """对外入口：返回 {salt_hex: enc_key_hex}。"""
    started = time.time()
    salt_index = collect_salts(db_storage)
    if verbose:
        print("数据库 %d 个，不同 salt %d 个" % (
            len([1 for _ in salt_index]), len(salt_index)))
    if not salt_index:
        return {}

    procs = winmem.weixin_processes()
    if not procs:
        if verbose:
            print("未检测到 Weixin.exe，请先启动并登录微信")
        return {}
    # Permission setup must not depend on whether logging is enabled.
    debug_enabled = winmem.enable_debug_privilege()
    if diagnostics is not None:
        diagnostics.update(processCount=len(procs), debugPrivilege=debug_enabled,
                           databases=len(salt_index), processesOpened=0, accessDenied=0)
    if verbose:
        print("微信进程：", ", ".join("%d(%dMB)" % (p["pid"], p["mem_kb"] // 1024) for p in procs[:4]))
        print("SeDebugPrivilege：", debug_enabled)

    found: dict[str, str] = {}
    readable_pids = []
    for proc in procs:
        if len(found) == len(salt_index):
            break
        stats = scan_process(proc["pid"], salt_index, found, verbose)
        if not stats.get("error"):
            readable_pids.append(proc["pid"])
        if diagnostics is not None:
            if stats.get("error"):
                diagnostics["accessDenied"] += 1
            else:
                diagnostics["processesOpened"] += 1
        if verbose:
            print("  PID %-6d 可读 %4dMB  needle=%-3d refs=%-4d nodes=%-2d blobs=%-2d cand=%-3d hit=%d" % (
                stats["pid"], stats["readable_mb"], stats["needle"], stats["refs"],
                stats["nodes"], stats["blobs"], stats["candidates"], stats["hit"]))
            if stats.get("error"):
                print("    ", stats["error"])
    if len(found) != len(salt_index):
        for pid in readable_pids:
            scan_legacy(pid, salt_index, found)
            if len(found) == len(salt_index):
                break
    # Only use the expensive passphrase fallback when nothing was found.
    if not found:
        budget = [64]
        for pid in readable_pids:
            scan_process(pid, salt_index, found, passphrase_budget=budget)
            if found or budget[0] <= 0:
                break
    if diagnostics is not None:
        diagnostics["resolved"] = len(found)
    if verbose:
        print("完成：%d/%d 个库拿到密钥，用时 %.1fs" % (
            len(found), len(salt_index), time.time() - started))
    return found
