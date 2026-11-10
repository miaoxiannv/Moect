"""Preset WeChat text-face assets; raw message text is never rewritten."""
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from .paths import is_link

TOKENS = re.compile(r'https?://[^\s<>"\']+|\[[^\[\]\r\n]{1,32}\]')


@lru_cache(maxsize=8)
def face_catalog(assets):
    try:
        data = json.loads((Path(assets) / 'wechat-faces.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    result = {}
    for name, entry in list(data.items())[:256]:
        if (isinstance(name, str) and re.fullmatch(r'\[[^\[\]\r\n]{1,32}\]', name)
                and isinstance(entry, dict)
                and re.fullmatch(r'[a-f0-9]{20}\.png', str(entry.get('file', '')))
                and re.fullmatch(r'[a-f0-9]{64}', str(entry.get('sha256', '')))):
            result[name] = entry
    return result


class ArchiveFaces:
    def __init__(self, assets, cancel=lambda: None):
        self.assets = Path(assets)
        self.catalog = face_catalog(str(self.assets.resolve()))
        self.cancel = cancel
        self.names = set()

    def remember_text(self, text):
        self.cancel()
        for match in TOKENS.finditer(str(text or '')):
            if match[0] in self.catalog:
                self.names.add(match[0])

    def remember(self, message, depth=0):
        if not isinstance(message, dict) or depth > 8:
            return
        self.remember_text(message.get('text'))
        extra = message.get('extra')
        extra = extra if isinstance(extra, dict) else {}
        self.remember(message.get('quote') or extra.get('reply'), depth + 1)
        forward = message.get('forward')
        children = forward.get('messages') if isinstance(forward, dict) else None
        for child in children if isinstance(children, list) else []:
            self.remember(child, depth + 1)

    def write(self, folder):
        mapping, copied = {}, {}
        for name in sorted(self.names):
            self.cancel()
            entry = self.catalog[name]
            filename = entry['file']
            if filename not in copied:
                copied[filename] = None
                source = self.assets / 'wechat-faces' / filename
                try:
                    if is_link(source) or source.stat().st_size > 256 * 1024:
                        continue
                    blob = source.read_bytes()
                    if (not blob.startswith(b'\x89PNG\r\n\x1a\n')
                            or hashlib.sha256(blob).hexdigest() != entry['sha256']):
                        continue
                    relative = Path('builtin-faces') / filename
                    destination = folder / relative
                    destination.parent.mkdir(exist_ok=True)
                    destination.write_bytes(blob)
                    copied[filename] = relative.as_posix()
                except OSError:
                    continue
            if copied[filename]:
                mapping[name] = copied[filename]
        script = 'window.MoectInlineFaces=' + json.dumps(mapping, ensure_ascii=False).replace('<', '\\u003c') + ';\n'
        (folder / 'builtin-faces.js').write_text(script, encoding='utf-8')
