"""Explicit per-account allowlist for message processing.

The source is a full encrypted database snapshot; this policy controls message
reads and analysis, not the scope of the database snapshot itself.
"""
from __future__ import annotations
import json
import os
import atomic_replace
import tempfile


def load_scope(directory, unprotect):
    try:
        with open(os.path.join(directory, "scope.bin"), "rb") as f:
            raw = unprotect(f.read())
        if raw is None:
            return set()
        obj = json.loads(raw.decode("utf-8"))
        values = obj.get("selected", [])
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            return set()
        return set(values)
    except (OSError, ValueError, UnicodeError, AttributeError):
        return set()


def save_scope(directory, selected, protect):
    os.makedirs(directory, exist_ok=True)
    sealed = protect(json.dumps({"version": 1, "selected": sorted(selected)}, ensure_ascii=False).encode("utf-8"))
    if sealed is None:
        raise RuntimeError("无法加密会话选择，未保存")
    fd, path = tempfile.mkstemp(prefix="scope-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(sealed)
            f.flush()
            os.fsync(f.fileno())
        atomic_replace.replace(path, os.path.join(directory, "scope.bin"))
    finally:
        if os.path.exists(path):
            os.unlink(path)


class ScopedReader:
    def __init__(self, reader, allowed):
        self._reader, self._allowed = reader, frozenset(allowed)

    def sessions(self, limit=200):
        # Filter before applying the analysis limit, otherwise excluded high-activity
        # conversations could crowd selected ones out of the results.
        return [x for x in self._reader.sessions(limit=10000) if x["username"] in self._allowed][:limit]

    def messages(self, username, limit=60, start=None, end=None):
        if username not in self._allowed:
            raise PermissionError("此会话未选择参与统计")
        if start is None and end is None:
            return self._reader.messages(username, limit=limit)
        return self._reader.messages(username, limit=limit, start=start, end=end)

    def messages_page(self, username, start, end, offset=0, limit=30):
        if username not in self._allowed:
            raise PermissionError("此会话未选择参与统计")
        return self._reader.messages_page(username, start, end, offset, limit)
