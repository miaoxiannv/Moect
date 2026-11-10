# -*- coding: utf-8 -*-
"""Windows 进程内存只读访问。只做三件事：枚举进程、枚举可读区域、按块读取。

不写内存、不注入、不创建远程线程。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import struct

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
READABLE = {0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80}
MAX_USER_ADDRESS = 0x0000_8000_0000_0000
CHUNK = 2 * 1024 * 1024


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_uint64),
        ("AllocationBase", ctypes.c_uint64),
        ("AllocationProtect", wt.DWORD),
        ("_pad1", wt.DWORD),
        ("RegionSize", ctypes.c_uint64),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
        ("_pad2", wt.DWORD),
    ]


kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.ReadProcessMemory.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)
]
kernel32.ReadProcessMemory.restype = wt.BOOL
kernel32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
kernel32.VirtualQueryEx.restype = ctypes.c_size_t
kernel32.GetCurrentProcess.restype = wt.HANDLE


def enable_debug_privilege() -> bool:
    """读取其它进程内存通常需要 SeDebugPrivilege；管理员身份下才拿得到。"""
    TOKEN_ADJUST_PRIVILEGES = 0x0020
    TOKEN_QUERY = 0x0008
    SE_PRIVILEGE_ENABLED = 0x00000002

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", LUID), ("Attributes", wt.DWORD)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wt.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]

    advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    advapi32.LookupPrivilegeValueW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.POINTER(LUID)]
    advapi32.AdjustTokenPrivileges.argtypes = [
        wt.HANDLE, wt.BOOL, ctypes.POINTER(TOKEN_PRIVILEGES), wt.DWORD,
        ctypes.POINTER(TOKEN_PRIVILEGES), ctypes.POINTER(wt.DWORD),
    ]

    token = wt.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, ctypes.byref(token)
    ):
        return False
    try:
        luid = LUID()
        if not advapi32.LookupPrivilegeValueW(None, "SeDebugPrivilege", ctypes.byref(luid)):
            return False
        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
        ctypes.set_last_error(0)
        ok = advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
        return bool(ok) and ctypes.get_last_error() == 0
    finally:
        kernel32.CloseHandle(token)


def weixin_processes() -> list[dict]:
    """列出 Weixin.exe，按常驻内存降序 —— 主进程通常最大，密钥在里面。"""
    try:
        import psutil
    except ImportError:
        return []
    out = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if (proc.info["name"] or "").lower() == "weixin.exe":
                try:
                    mem = proc.memory_info().rss // 1024
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    mem = 0
                out.append({"pid": proc.info["pid"], "mem_kb": mem})
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
            continue
    out.sort(key=lambda x: x["mem_kb"], reverse=True)
    return out


def open_process(pid: int):
    return kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)


def read_memory(handle, address: int, size: int) -> bytes | None:
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(handle, address, buf, size, ctypes.byref(got))
    if not ok:
        return None
    return buf.raw[: got.value]


def readable_regions(handle) -> list[tuple[int, int]]:
    regions: list[tuple[int, int]] = []
    address = 0
    mbi = MBI()
    while address < 0x7FFF_FFFF_FFFF:
        if kernel32.VirtualQueryEx(handle, address, ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break
        base, size = mbi.BaseAddress, mbi.RegionSize
        if mbi.State == MEM_COMMIT and mbi.Protect in READABLE and 0 < size < 512 * 1024 * 1024:
            regions.append((base, size))
        nxt = base + size
        if nxt <= address:
            break
        address = nxt
    return regions


def iter_chunks(handle, regions, overlap: int = 0):
    """按块读取区域，overlap 保证跨块的特征不丢。"""
    for base, size in regions:
        offset = 0
        tail = b""
        tail_base = base
        while offset < size:
            cur = min(CHUNK, size - offset)
            chunk = read_memory(handle, base + offset, cur) or b""
            data_base = tail_base if tail else base + offset
            data = tail + chunk
            if data:
                yield data_base, data
                tail = data[-overlap:] if overlap else b""
                tail_base = data_base + max(0, len(data) - len(tail))
            else:
                tail = b""
                tail_base = base + offset + cur
            offset += cur


def u64(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 8 > len(data):
        return 0
    return struct.unpack_from("<Q", data, offset)[0]
