"""Encrypted, resumable account-local analysis checkpoint."""
import json
import os
import atomic_replace
import tempfile

NAME = 'analysis-job.bin'

def load(directory, unprotect):
    try:
        with open(os.path.join(directory, NAME), 'rb') as f:
            blob = f.read()
    except FileNotFoundError:
        return None
    raw = unprotect(blob)
    if raw is None:
        raise ValueError('分析进度解密失败，原文件已保留')
    obj = json.loads(raw.decode('utf-8'))
    if not isinstance(obj, dict) or obj.get('version') != 1:
        raise ValueError('分析进度格式错误，原文件已保留')
    return obj

def save(directory, job, protect):
    blob = protect(json.dumps(job, ensure_ascii=False).encode('utf-8'))
    if blob is None:
        raise ValueError('无法加密分析进度')
    os.makedirs(directory, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='analysis-job-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        atomic_replace.replace(name, os.path.join(directory, NAME))
    finally:
        if os.path.exists(name): os.unlink(name)
