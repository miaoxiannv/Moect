"""Explicit, cancellable image recovery confined to the connected account."""
import heapq
import json
import os
import time
from pathlib import Path

from .media_codec import ImageKeys
from .paths import is_link
from wx4.media import SIG_V2, decrypt_dat_prefix, find_dat_aes_key, read_dat_key_probe


def recent_image_samples(data, cancel=lambda: None, limit=40, seconds=8):
    if limit <= 0 or seconds <= 0:
        return []
    account = Path(data.account_dir()).resolve()
    started = time.monotonic()
    deadline = started + seconds
    discovery_deadline = started + seconds / 2
    attach = account / 'msg/attach'
    ranked = []
    recent = []
    visited = 0
    seen = set()

    def sample_directory(base, until, max_files=50000):
        nonlocal visited
        batch_end = min(50000, visited + max_files)
        cancel()
        if visited >= batch_end or time.monotonic() >= until or not base.is_dir() or is_link(base):
            return
        for directory, dirs, files in os.walk(base, followlinks=False):
            cancel()
            if visited >= batch_end or time.monotonic() >= until:
                return
            dirs[:] = [name for name in dirs if not is_link(Path(directory) / name)]
            for name in files:
                cancel()
                if visited >= batch_end or time.monotonic() >= until:
                    return
                visited += 1
                path = Path(directory) / name
                if path in seen:
                    continue
                seen.add(path)
                if is_link(path) or not path.resolve().is_relative_to(account):
                    continue
                try:
                    stat = path.stat()
                    if not 31 <= stat.st_size <= 128 * 1024 * 1024:
                        continue
                    sample = (stat.st_mtime, str(path))
                    if len(recent) == limit and sample <= recent[0]:
                        continue
                    with path.open('rb') as source:
                        if source.read(6) != SIG_V2:
                            continue
                    # Publish each verified sample immediately: a later timeout
                    # must not discard files already checked in this directory.
                    if len(recent) < limit:
                        heapq.heappush(recent, sample)
                    else:
                        heapq.heapreplace(recent, sample)
                except OSError:
                    continue

    def children(parent):
        cancel()
        if time.monotonic() >= discovery_deadline or not parent.is_dir() or is_link(parent):
            return
        try:
            for path in parent.iterdir():
                cancel()
                if time.monotonic() >= discovery_deadline:
                    return
                if not is_link(path) and path.is_dir() and path.resolve().is_relative_to(account):
                    yield path
        except OSError:
            return

    def add_directory(directory):
        if not directory.is_dir() or is_link(directory) or not directory.resolve().is_relative_to(account):
            return
        if not recent:
            # Empty/non-V2 directories must not disable early sampling in later
            # directories. Each short batch still shares the discovery deadline.
            until = min(discovery_deadline, time.monotonic() + min(0.25, seconds / 8))
            sample_directory(directory, until, max_files=limit)
        try:
            ranked.append((directory.stat().st_mtime, str(directory)))
        except OSError:
            pass

    # Directory discovery only prioritizes traversal; reserve half the total
    # budget for file reads even when directory metadata is slow.
    for session in children(attach):
        if time.monotonic() >= discovery_deadline:
            break
        for month in children(session):
            if time.monotonic() >= discovery_deadline:
                break
            add_directory(month / 'Img')
    for month in children(account / 'cache'):
        if time.monotonic() >= discovery_deadline:
            break
        add_directory(month / 'Sns')

    # Overwrites need not update directory times. Keep comparing actual file
    # times across directories after the heap reaches its sample limit.
    bases = [Path(path) for _, path in sorted(ranked, reverse=True)]
    bases.append(attach)  # Retain bounded fallback for older/unusual layouts.
    for base in bases:
        cancel()
        if visited >= 50000 or time.monotonic() >= deadline:
            break
        sample_directory(base, deadline)
    return [Path(row[1]) for row in sorted(recent, reverse=True)]


def recover_local_images(data, cancel, status, timeout=45):
    status('正在检查当前账号的近期图片缓存…')
    paths = recent_image_samples(data, cancel)
    if not paths:
        raise ValueError('当前账号未找到可用于恢复的本地 V2 图片。请先在微信打开需要的图片，再重试。')
    diagnostics = {}
    status('正在读取并严格校验当前账号的图片密钥，可随时取消…')
    key = find_dat_aes_key(str(paths[0]), sample_paths=paths[1:], cancel=cancel,
                           timeout=timeout, diagnostics=diagnostics, wxid=data.wxid)
    cancel()
    if not key:
        if diagnostics.get('processes_found') == 0:
            raise ValueError('未检测到电脑微信，请打开并登录微信后重试。')
        if diagnostics.get('processes_readable') == 0:
            raise ValueError('无法读取微信进程。若微信以管理员运行，请以同等权限打开 Moect 后重试。')
        raise ValueError('本地图片已找到，但本次尚未取得通过完整校验的图片密钥。请在微信打开一张近期原图后重试。')
    keys = ImageKeys(data, cancel, progress=status)
    keys.scan_remaining = 0
    verified = None
    for path in paths:
        cancel()
        probe = read_dat_key_probe(path)
        if probe is not None and decrypt_dat_prefix(probe, key) is not None:
            verified = probe
            break
    if verified is None:
        raise ValueError('图片候选密钥未通过完整校验，未保存，请重试。')
    cancel()
    keys._remember(key, verified)
    try:
        saved = json.loads(keys.path.read_text(encoding='utf-8'))
        if key.hex() not in saved.get('keys', []):
            raise ValueError()
    except (OSError, ValueError):
        raise ValueError('图片解密材料已验证，但无法保存到当前缓存目录，请检查目录权限和可用空间。') from None
    decoded = False
    for path in paths[:8]:
        cancel()
        try:
            raw = path.read_bytes()
            plain = keys.decode(raw, path, paths)
            if keys.rendered or plain and keys.codec.image(plain):
                decoded = True
                break
        except OSError:
            continue
    return {'samples': len(paths), 'aes_verified': True, 'decoded_sample': decoded}
