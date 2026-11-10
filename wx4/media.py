# -*- coding: utf-8 -*-
"""WeChat DAT images: legacy XOR, V1 fixed AES, V2 account AES + XOR.

V4 headers store AES plaintext length, XOR tail length and a format flag.
Image keys are account-scoped and independent of database encryption keys.
Browser conversion and protected key caching live in core.media_codec.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import os
import re
import shutil
import struct
import time
from dataclasses import dataclass, field
from itertools import chain, islice

from Cryptodome.Cipher import AES

SIG_V1 = bytes.fromhex("070856310807")
SIG_V2 = bytes.fromhex("070856320807")
V1_KEY = hashlib.md5(b"0").hexdigest()[:16].encode()

IMG_EXT = ((b"\xff\xd8\xff", ".jpg"), (b"\x89PNG", ".png"), (b"GIF8", ".gif"),
           (b"RIFF", ".webp"), (b"BM", ".bmp"), (b"wxgf", ".wxgf"))

kernel32 = ctypes.windll.kernel32
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
READABLE = {0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80}


class _MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_uint64), ("AllocationBase", ctypes.c_uint64),
                ("AllocationProtect", wt.DWORD), ("_p1", wt.DWORD),
                ("RegionSize", ctypes.c_uint64), ("State", wt.DWORD),
                ("Protect", wt.DWORD), ("Type", wt.DWORD), ("_p2", wt.DWORD)]


# ---------------------------------------------------------------------------
# dat 解码
# ---------------------------------------------------------------------------
def sniff_ext(data: bytes) -> str:
    for magic, ext in IMG_EXT:
        if data.startswith(magic):
            return ext
    return ".bin"


def _pkcs7_strip(data: bytes) -> bytes | None:
    if data and 1 <= data[-1] <= 16 and data[-data[-1]:] == bytes([data[-1]]) * data[-1]:
        return data[:-data[-1]]
    return None


def dat_layout(data: bytes):
    """V4 has a 15-byte header and a PKCS#7 padded AES prefix.

    Byte 14 is a flag, not the separately stored account XOR key. A full
    padding block is present even when aes_size is already divisible by 16.
    """
    if len(data)<31 or data[:6] not in (SIG_V1,SIG_V2):return None
    aes_size,xor_size=struct.unpack_from('<LL',data,6)
    aligned=(aes_size//16+1)*16
    end=15+aligned
    tail=len(data)-xor_size
    if aes_size<=0 or end>len(data) or tail<end:return None
    return data[15:end],data[end:tail],data[tail:],data[14]


def image_prefix(data):
    if data.startswith((b'\xff\xd8\xff',b'\x89PNG\r\n\x1a\n',b'GIF8',b'BM',b'wxgf')):return True
    return data.startswith(b'RIFF') and data[8:12]==b'WEBP'


def decrypt_dat_prefix(data, key):
    """Validate a key with the COMPLETE AES segment, padding and declared size.

    A two-byte BMP signature alone produces frequent false positives during a
    process scan. It must never be sufficient to accept or cache an AES key.
    """
    lay=dat_layout(data)
    if not lay or not key or len(key)!=16:return None
    try:plain=_pkcs7_strip(AES.new(key,AES.MODE_ECB).decrypt(lay[0]))
    except ValueError:return None
    if plain is None or len(plain)!=struct.unpack_from('<L',data,6)[0]:return None
    return plain if image_prefix(plain) else None


def infer_xor_key(data, prefix=b''):
    lay=dat_layout(data)
    if not lay or not lay[2]:return None
    tail=lay[2]
    endings=[]
    if prefix.startswith(b'\xff\xd8'):endings=[b'\xff\xd9']
    elif prefix.startswith(b'\x89PNG'):endings=[b'IEND\xaeB`\x82']
    for ending in endings:
        if len(tail)<len(ending):continue
        keys={a^b for a,b in zip(tail[-len(ending):],ending)}
        if len(keys)==1:return keys.pop()
    return None


def decode_dat(data: bytes, aes_key: bytes | None=None, xor_key: int | None=None):
    if image_prefix(data):return data
    if data[:6] not in (SIG_V1,SIG_V2):
        for k in range(256):
            if image_prefix(bytes(b^k for b in data[:16])):
                return bytes(b^k for b in data)
        return None
    key=V1_KEY if data[:6]==SIG_V1 else aes_key
    if not key or len(key)!=16:return None
    lay=dat_layout(data)
    if not lay:return None
    _aes_ct,mid,xor_ct,_flag=lay
    prefix=decrypt_dat_prefix(data,key)
    if prefix is None:return None
    if xor_ct:
        xk=infer_xor_key(data,prefix)
        if xk is None:xk=xor_key
        if xk is None:return None
        return prefix+mid+bytes(b^(xk&255) for b in xor_ct)
    return prefix+mid


def read_dat_key_probe(path, limit=128 * 1024 * 1024):
    """Read the complete AES segment without loading the clear/XOR image body."""
    try:
        with open(path, 'rb') as source:
            header = source.read(15)
            if len(header) != 15 or header[:6] != SIG_V2:
                return None
            aes_size, xor_size = struct.unpack_from('<LL', header, 6)
            encrypted_size = (aes_size // 16 + 1) * 16
            if aes_size <= 0 or 15 + encrypted_size > limit or 15 + encrypted_size > os.fstat(source.fileno()).st_size - xor_size:
                return None
            ciphertext = source.read(encrypted_size)
            if len(ciphertext) != encrypted_size:
                return None
    except OSError:
        return None
    # Preserve the declared plaintext length and all AES bytes. The independent
    # clear body and XOR tail are irrelevant to validating an AES candidate.
    return header[:10] + b'\0' * 4 + header[14:] + ciphertext


def _dat_key_candidates(buffer):
    """Try complete key-like strings before sliding through large text runs."""
    runs = list(re.finditer(rb'[A-Za-z0-9]{16,}', buffer))
    wide_runs = list(re.finditer(rb'(?:[A-Za-z0-9]\x00){16,}', buffer))
    for match in runs:
        yield buffer[match.start():match.start() + 16]
        if match.end() - match.start() in (32, 64):
            value = buffer[match.start():match.start() + 32]
            if re.fullmatch(rb'[a-fA-F0-9]{32}', value):
                yield bytes.fromhex(value.decode('ascii'))
    for match in re.finditer(rb'[\x0a\x12\x1a\x22]\x10(.{16})', buffer, re.S):
        yield match.group(1)
    for match in wide_runs:
        value = match.group()[::2]
        yield value[:16]
        if len(value) in (32, 64) and re.fullmatch(rb'[a-fA-F0-9]{32}', value[:32]):
            yield bytes.fromhex(value[:32].decode('ascii'))
    for match in runs:
        for offset in range(match.start() + 1, match.end() - 15):
            yield buffer[offset:offset + 16]
    for match in wide_runs:
        value = match.group()[::2]
        for offset in range(1, len(value) - 15):
            yield value[offset:offset + 16]


def _image_key_buffers(handle, regions, deadline, cancel):
    from . import key as memory
    for _priority, base, size in regions:
        for offset in range(0, size, 4 * 1024 * 1024):
            cancel()
            if time.monotonic() >= deadline:
                return
            length = min(size - offset, 4 * 1024 * 1024 + 128)
            buffer = ctypes.create_string_buffer(length)
            count = ctypes.c_size_t()
            memory.kernel32.ReadProcessMemory(handle, ctypes.c_void_p(base + offset), buffer,
                                               length, ctypes.byref(count))
            if count.value:
                yield base + offset, buffer.raw[:count.value]


def _account_image_key_candidates(handle, regions, wxid, deadline, cancel, diagnostics=None):
    """Derive candidates from this account's config; acceptance still requires DAT validation.

    In the 4.1 config layout the image seed precedes the wxid std::string by
    eight bytes. Locate the actual account string and its string objects rather
    than relying on a version-specific DLL address or scanning arbitrary seeds.
    Derivation reference: https://github.com/jiatj/wechatapi/blob/main/wechatauto/media.py
    """
    from . import key as memory
    account = wxid.encode('utf-8')
    if not 1 <= len(account) <= 128:
        return
    heaps = [region for region in regions if region[0] == 0]
    addresses = set()
    objects = set()
    long_account = len(account) > 15
    now = time.monotonic()
    # Leave time to revisit objects that precede their heap-allocated string.
    discovery_deadline = now + max(0., deadline - now) / 2 if long_account else deadline

    def candidate(address):
        if address in objects or address < 0x10008:
            return None
        objects.add(address)
        if diagnostics is not None:
            diagnostics['account_objects'] = diagnostics.get('account_objects', 0) + 1
        record = memory._read(handle, address - 8, 40)
        if not record or len(record) != 40:
            return None
        length, capacity = struct.unpack_from('<QQ', record, 24)
        if length != len(account) or not length <= capacity < 0x100000:
            return None
        seed = struct.unpack_from('<I', record)[0]
        return hashlib.md5((str(seed) + wxid).encode('utf-8')).hexdigest()[:16].encode('ascii')

    def pointer_candidates(base, buffer, until):
        for address in addresses:
            cancel()
            if time.monotonic() >= until:
                return
            pointer = struct.pack('<Q', address)
            offset = buffer.find(pointer)
            while offset >= 0:
                cancel()
                if time.monotonic() >= until:
                    return
                value = candidate(base + offset)
                if value is not None:
                    yield value
                offset = buffer.find(pointer, offset + 1)

    for base, buffer in _image_key_buffers(handle, heaps, discovery_deadline, cancel):
        offset = buffer.find(account + b'\0')
        while offset >= 0:
            cancel()
            if time.monotonic() >= discovery_deadline:
                break
            address = base + offset
            if address not in addresses:
                addresses.add(address)
                if diagnostics is not None:
                    diagnostics['account_strings'] = diagnostics.get('account_strings', 0) + 1
            if not long_account:
                value = candidate(base + offset)
                if value is not None:
                    yield value
            offset = buffer.find(account + b'\0', offset + 1)
            if len(addresses) >= 256:
                break
        if long_account and addresses:
            # Check the current chunk before another slow memory read can use
            # the remaining budget. No full-process buffer cache is retained.
            yield from pointer_candidates(base, buffer, discovery_deadline)
        if len(addresses) >= 256 or time.monotonic() >= discovery_deadline:
            break
    if long_account and addresses:
        for base, buffer in _image_key_buffers(handle, heaps, deadline, cancel):
            yield from pointer_candidates(base, buffer, deadline)


def find_dat_aes_key(sample_path: str,pids=None,progress=None,cancel=None,timeout=30,sample_paths=(),diagnostics=None,wxid=''):
    """Find account image keys using validated prefixes, without alignment assumptions.

    Includes 16/32/64-character strings, hex-encoded raw AES keys and binary
    protobuf key fields. Candidates must decrypt an actual image or WXGF header.
    An optional wxid enables a bounded account-config derivation pass first.
    No credentials, process bytes or recovered keys are logged.
    """
    from . import key as memory
    cancel=cancel or (lambda:None)
    templates = []
    seen_probes = set()
    remaining_bytes = 128 * 1024 * 1024
    for path in chain((sample_path,), islice(sample_paths, 40)):
        cancel()
        probe = read_dat_key_probe(path, remaining_bytes)
        if probe is None:
            continue
        signature = hashlib.sha256(probe).digest()
        if signature in seen_probes:
            continue
        # A damaged original and a good thumbnail can share the same first AES
        # block. Keep both complete probes so damage cannot hide the valid one.
        seen_probes.add(signature)
        templates.append(probe)
        remaining_bytes -= len(probe)
        if len(templates) >= 8:
            break
    if not templates:
        return None
    blocks = b''.join(probe[15:31] for probe in templates)
    if pids is None:pids=[p['pid'] for p in memory.find_weixin_processes()]
    if diagnostics is not None:diagnostics.update(processes_found=len(pids),processes_readable=0)
    deadline=time.monotonic()+timeout
    checked=0;seen=set()
    memory.enable_debug_privilege()

    def verified(candidate):
        plaintext = AES.new(candidate, AES.MODE_ECB).decrypt(blocks)
        return any(image_prefix(plaintext[index * 16:(index + 1) * 16])
                   and decrypt_dat_prefix(probe, candidate) is not None
                   for index, probe in enumerate(templates))

    for pid_index,pid in enumerate(pids):
        remaining=deadline-time.monotonic()
        if remaining<=0:return None
        share=.7 if pid_index==0 and len(pids)>1 else 1/(len(pids)-pid_index)
        pid_deadline=min(deadline,time.monotonic()+max(2,remaining*share))
        h=memory.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION|PROCESS_VM_READ,False,pid)
        if not h:continue
        if diagnostics is not None:diagnostics['processes_readable']+=1
        try:
            address=0;mbi=memory._MBI();regions=[]
            while address<memory.MAX_USER_ADDRESS and time.monotonic()<pid_deadline:
                cancel()
                if not memory.kernel32.VirtualQueryEx(h,ctypes.c_void_p(address),ctypes.byref(mbi),ctypes.sizeof(mbi)):break
                base,size=mbi.BaseAddress,mbi.RegionSize;address=base+size
                if address<=base:break
                if mbi.State!=MEM_COMMIT or mbi.Protect&0x100 or (mbi.Protect&0xff) not in READABLE:continue
                # Mutable private heaps contain the live config and key material.
                # Read-only strings must not exhaust the scan budget first.
                priority=(0 if mbi.Type==0x20000 and mbi.Protect&0xff in (0x04,0x08,0x40,0x80) else 1)
                regions.append((priority,base,size))
            regions.sort()
            if wxid:
                # Configuration layouts can change. Give this optional path at
                # most half the remaining process budget (capped at 10 seconds)
                # so the existing string search still gets a chance to run.
                now = time.monotonic()
                config_deadline = now + min(10., max(0., (pid_deadline - now) / 2))
                for cand in _account_image_key_candidates(h, regions, wxid, config_deadline, cancel, diagnostics):
                    if verified(cand):
                        if diagnostics is not None:diagnostics['key_source'] = 'account_config'
                        return cand
            for _base, buffer in _image_key_buffers(h, regions, pid_deadline, cancel):
                for cand in _dat_key_candidates(buffer):
                    checked+=1
                    if checked%256==0:
                        cancel()
                        if time.monotonic()>=pid_deadline:break
                    if cand in seen:continue
                    if len(seen)<250000:seen.add(cand)
                    if verified(cand):
                        if diagnostics is not None:diagnostics['key_source'] = 'process_string'
                        return cand
        finally:memory.kernel32.CloseHandle(h)
    return None


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------
@dataclass
class MediaStats:
    images: int = 0
    images_raw: int = 0
    voices: int = 0
    videos: int = 0
    files: int = 0
    extras: list = field(default_factory=list)


def export_voice(data, msgs, out_dir: str, progress=None) -> int:
    """导出语音为 .silk（SILK 格式，可用 silk-v3-decoder 转 mp3）。"""
    progress = progress or (lambda m: None)
    n = 0
    os.makedirs(out_dir, exist_ok=True)
    for m in msgs:
        if m["type"] != 34 or not m["server_id"]:
            continue
        blob = data.voice_blob(m["session"], m["server_id"])
        if not blob:
            continue
        stamp = m["time_str"].replace(":", "").replace("-", "").replace(" ", "_")
        path = os.path.join(out_dir, "voice_%s_%s.silk" % (stamp, m["server_id"]))
        with open(path, "wb") as f:
            f.write(blob)
        m["extra"]["voice_file"] = path
        n += 1
    if n:
        progress("语音 %d 条 -> %s" % (n, out_dir))
    return n


def _session_attach_index(attach_dir: str) -> dict:
    idx = {}
    if not attach_dir or not os.path.isdir(attach_dir):
        return idx
    for root, _dirs, files in os.walk(attach_dir):
        for f in files:
            if f.endswith(".dat"):
                idx.setdefault(os.path.splitext(f)[0], os.path.join(root, f))
    return idx


def export_images(data, session: str, msgs, out_dir: str,
                  aes_key: bytes | None = None, copy_dat: bool = True,
                  progress=None) -> int:
    """按消息里的 md5 把图片找出来；V1 自动解密，V2 视 copy_dat 决定是否原样复制。"""
    progress = progress or (lambda m: None)
    attach_dir = data.attach_dir(session)
    if not attach_dir:
        return 0
    index = _session_attach_index(attach_dir)
    if not index:
        return 0
    os.makedirs(out_dir, exist_ok=True)
    n = 0
    for m in msgs:
        if m["type"] != 3:
            continue
        md5s = re.findall(r"\b[0-9a-f]{32}\b", m.get("content") or "")
        md5s += re.findall(r"\b[0-9a-f]{32}\b", m.get("packed_hex") or "")
        for md5 in dict.fromkeys(md5s):
            src = (index.get(md5) or index.get(md5 + "_h") or index.get(md5 + "_t"))
            if not src:
                continue
            with open(src, "rb") as f:
                raw = f.read()
            out = decode_dat(raw, aes_key, None)
            stamp = m["time_str"].replace(":", "").replace("-", "").replace(" ", "_")
            if out is not None:
                dst = os.path.join(out_dir, "img_%s_%s%s" % (stamp, md5[:8], sniff_ext(out)))
                with open(dst, "wb") as f:
                    f.write(out)
            elif copy_dat:
                dst = os.path.join(out_dir, "%s.dat" % md5)
                if not os.path.exists(dst):
                    shutil.copy2(src, dst)
            else:
                continue
            m["extra"].setdefault("images", []).append(dst)
            n += 1
            break
    if n:
        progress("图片 %d 张 -> %s" % (n, out_dir))
    return n


def copy_plain_media(data, out_dir: str, progress=None) -> tuple:
    """视频/文件在 msg/video、msg/file 下是明文，整体复制。"""
    progress = progress or (lambda m: None)
    nv = nf = 0
    for sub in ("video", "file"):
        base = os.path.join(data.msg_dir(), sub)
        if not os.path.isdir(base):
            continue
        dst_dir = os.path.join(out_dir, sub)
        for root, _dirs, files in os.walk(base):
            for f in files:
                src = os.path.join(root, f)
                dst = os.path.join(dst_dir, os.path.relpath(src, base))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                try:
                    shutil.copy2(src, dst)
                except OSError:
                    continue
                if sub == "video":
                    nv += 1
                else:
                    nf += 1
    if nv or nf:
        progress("视频 %d 个、文件 %d 个 -> %s" % (nv, nf, out_dir))
    return nv, nf


def export_session_media(data, session: str, msgs, out_root: str,
                         aes_key=None, copy_dat=True, progress=None) -> MediaStats:
    """导出某个会话的全部媒体，返回统计。"""
    progress = progress or (lambda m: None)
    os.makedirs(out_root, exist_ok=True)
    st = MediaStats()
    st.images = export_images(data, session, msgs, os.path.join(out_root, "image"),
                              aes_key=aes_key, copy_dat=copy_dat, progress=progress)
    st.voices = export_voice(data, msgs, os.path.join(out_root, "voice"), progress=progress)
    return st
