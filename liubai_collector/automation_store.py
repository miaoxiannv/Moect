"""Encrypted automation state and date-indexed agent insights."""
import hashlib
import json
import os
import atomic_replace
import tempfile
from datetime import datetime


def _path(directory, name):
    return os.path.join(directory, name)


def load(directory, name, unprotect, default):
    try:
        with open(_path(directory, name), 'rb') as f: blob = f.read()
    except FileNotFoundError:
        return default
    raw = unprotect(blob)
    if raw is None: raise ValueError('自动分析数据解密失败，原文件已保留')
    obj = json.loads(raw.decode('utf-8'))
    if not isinstance(obj, type(default)): raise ValueError('自动分析数据格式错误，原文件已保留')
    return obj


def save(directory, name, value, protect):
    blob = protect(json.dumps(value, ensure_ascii=False).encode('utf-8'))
    if blob is None: raise ValueError('无法加密保存自动分析数据')
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='auto-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(blob); f.flush(); os.fsync(f.fileno())
        atomic_replace.replace(tmp, _path(directory, name))
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def merge_daily(directory, insights, protect, unprotect):
    """Merge only evidence-backed Agent output, grouped by local evidence day."""
    groups = {}
    for item in insights:
        if item.get('category') == 'task_update' or not item.get('sources'): continue
        ref = item['sources'][0]
        if not ref.get('conversationId') or not ref.get('at'): continue
        date = datetime.fromtimestamp(ref['at']).strftime('%Y-%m-%d')
        groups.setdefault(date, []).append(item)
    for date, entries in groups.items():
        name = 'daily-' + date + '.bin'
        current = load(directory, name, unprotect, [])
        def key(x):
            ref = (x.get('sources') or [{}])[0]
            raw = '\0'.join([str(x.get('category','')),str(ref.get('conversationId','')),
                             str(ref.get('serverId') or ref.get('at') or ''),str(x.get('title',''))])
            return hashlib.sha256(raw.encode('utf-8')).hexdigest()
        seen = {key(x) for x in current}
        for item in entries:
            k = key(item)
            if k not in seen: current.append(item); seen.add(k)
        current.sort(key=lambda x: -(x.get('at') or 0))
        save(directory, name, current, protect)


def read_daily(directory, date, allowed, unprotect):
    datetime.strptime(date, '%Y-%m-%d')
    values = load(directory, 'daily-' + date + '.bin', unprotect, [])
    return [item for item in values if item.get('sources') and all(
        ref.get('conversationId') in allowed for ref in item['sources'])]


def save_summary(directory, date, summary, protect):
    datetime.strptime(date, '%Y-%m-%d')
    save(directory, 'summary-' + date + '.bin', summary, protect)


def read_summary(directory, date, allowed, unprotect):
    datetime.strptime(date, '%Y-%m-%d')
    summary = load(directory, 'summary-' + date + '.bin', unprotect, {})
    if not summary: return None
    # A sentence can combine multiple sources: hide the whole point if any
    # referenced conversation has since been removed from the allowlist.
    return dict(summary, points=[p for p in summary.get('points', []) if p.get('sources') and all(
        ref.get('conversationId') in allowed for ref in p['sources'])])
