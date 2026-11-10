# -*- coding: utf-8 -*-
"""把解密后的库读成会话与消息。

微信 4.x 的结构：
  session/session.db   -> SessionTable（会话与最后一条摘要）
  contact/contact.db   -> contact / stranger / chat_room / chatroom_member
  message/message_N.db -> Msg_<md5(会话名)>，每个会话一张表，跨分片
  message/message_N.db -> Name2Id（rowid <-> user_name）
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
from datetime import datetime

TYPE_NAMES = {
    1: "文本", 3: "图片", 34: "语音", 37: "好友请求", 42: "名片", 43: "视频",
    47: "动画表情", 48: "位置", 49: "应用消息", 50: "音视频通话", 51: "状态",
    62: "小视频", 66: "连线", 10000: "系统消息", 10002: "系统消息",
    11000: "动画表情", 11001: "引用消息", 11002: "文件",
}
KNOWN_BASE = set(TYPE_NAMES) | set(range(1, 100)) | {10000, 10002}
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

try:
    import zstandard as zstd
    _ZSTD = zstd.ZstdDecompressor()
except ImportError:  # pragma: no cover
    _ZSTD = None


def _text_factory(value: bytes):
    if b"\x00" in value:
        return value
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value


def open_db(path: str):
    if not os.path.exists(path):
        return None
    con = sqlite3.connect("file:" + path.replace("\\", "/") + "?mode=ro", uri=True, check_same_thread=False)
    con.text_factory = _text_factory
    con.row_factory = sqlite3.Row
    return con


def tables_of(con) -> set:
    try:
        return {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    except sqlite3.Error:
        return set()


def normalize_type(value) -> int:
    """4.x 的 local_type 可能是 (subtype << 32) | base。"""
    try:
        t = int(value)
    except (TypeError, ValueError):
        return 0
    if t <= 0xFFFF:
        return t
    base = t & 0xFFFFFFFF
    return base if base in KNOWN_BASE else base & 0xFF


def to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        if raw[:4] == ZSTD_MAGIC and _ZSTD is not None:
            try:
                return _ZSTD.decompress(raw, max_output_size=4 * 1024 * 1024).decode("utf-8", "ignore")
            except Exception:  # noqa: BLE001
                return ""
        txt = raw.decode("utf-8", "ignore")
        start = txt.find("<")
        if 0 < start <= 24 and len(txt) > start:
            txt = txt[start:]
        end = txt.find("\x01\x00")
        if end > 0:
            txt = txt[:end]
        return txt
    return str(value)


def decode_content(message_content, compress_content) -> str:
    for blob in (message_content, compress_content):
        if isinstance(blob, (bytes, bytearray)) and bytes(blob)[:4] == ZSTD_MAGIC:
            t = to_text(blob)
            if t:
                return t
    for blob in (compress_content, message_content):
        t = to_text(blob)
        if t and t.strip():
            return t.strip()
    return ""


def msg_table_for(username: str) -> str:
    return "Msg_" + hashlib.md5(username.encode("utf-8")).hexdigest()


_XML_TITLE = re.compile(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", re.S)


def humanize(content: str, mtype: int) -> str:
    """把 XML 形态的消息压成一行人能读的文字。原始 XML 不进界面。"""
    text = (content or "").strip()
    if not text.startswith("<"):
        return text
    low = text.lower()
    if "<emoji" in low:
        return "[表情]"
    if "<img" in low or "<msgimg" in low:
        return "[图片]"
    if "<voicemsg" in low:
        return "[语音]"
    if "<videomsg" in low or "<shortvideo" in low:
        return "[视频]"
    if "<location" in low:
        label = re.search(r'label="([^"]*)"', text)
        return "[位置] " + (label.group(1) if label else "")
    if "<appmsg" in low:
        m = _XML_TITLE.search(text)
        title = (m.group(1).strip() if m else "")[:60]
        if "<type>57<" in text or "<refermsg" in low:
            return "[引用] " + title
        if "<type>6<" in text:
            return "[文件] " + title
        if "<type>19<" in text or "<type>20<" in text or "<recorditem" in low:
            return "[聊天记录] " + title
        if "<type>2000<" in text:
            return "[转账]"
        if "<type>2001<" in text or "<wcpayinfo" in low:
            return "[红包]"
        if "<type>33<" in text or "<type>36<" in text:
            return "[小程序] " + title
        if "<type>5<" in text:
            return "[链接] " + title
        return ("[应用消息] " + title).strip()
    if "<sysmsg" in low or "<revokemsg" in low:
        return "[系统消息]"
    if mtype in (10000, 10002):
        return "[系统消息]"
    return "[结构化消息]"


class WxReader:
    """读解密后的快照目录。只读，不修改任何快照文件。"""

    def __init__(self, root: str):
        self.root = root
        self._lock = threading.RLock()
        self._conns: dict[str, sqlite3.Connection | None] = {}
        self._msg_dbs: list[sqlite3.Connection] = []
        self._contacts: dict[str, dict] = {}
        self._room_members: dict[str, list[str]] = {}
        self._open_all()

    def _open(self, rel: str):
        if rel in self._conns:
            return self._conns[rel]
        con = open_db(os.path.join(self.root, rel.replace("/", os.sep)))
        self._conns[rel] = con
        return con

    def _open_all(self):
        for n in range(4):
            con = self._open("message/message_%d.db" % n)
            if con is not None:
                self._msg_dbs.append(con)
        self._load_contacts()
        self._load_rooms()

    def _load_contacts(self):
        con = self._open("contact/contact.db")
        if con is None:
            return
        available = tables_of(con)
        for table in ("contact", "stranger"):
            if table not in available:
                continue
            try:
                rows = con.execute('select * from "%s"' % table)
            except sqlite3.Error:
                continue
            for row in rows:
                d = dict(row)
                for field in ("username", "user_name", "remark", "nick_name", "alias"):
                    if isinstance(d.get(field), bytes):
                        try:
                            d[field] = d[field].decode("utf-8")
                        except UnicodeDecodeError:
                            d[field] = ""
                username = d.get("username") or d.get("user_name") or ""
                if username:
                    self._contacts.setdefault(username, d)

    def _load_rooms(self):
        con = self._open("contact/contact.db")
        if con is None:
            return
        try:
            room_ids = {r["id"]: r["username"] for r in con.execute("select id,username from chat_room")}
            rows = con.execute("select room_id,member_id from chatroom_member").fetchall()
        except sqlite3.Error:
            return
        for r in rows:
            room = room_ids.get(r["room_id"])
            if not room:
                continue
            member = self._contacts.get("#id:%s" % r["member_id"])
            if member:
                self._room_members.setdefault(room, []).append(member.get("username", ""))

    def name_of(self, username: str) -> str:
        c = self._contacts.get(username)
        if c:
            for key in ("remark", "nick_name", "alias", "username"):
                v = c.get(key)
                if v:
                    return str(v)
        if username.endswith("@chatroom"):
            members = self._room_members.get(username) or []
            if members:
                names = [self.name_of(m) for m in members[:3]]
                return "%s(%d)" % ("、".join(n for n in names if n), len(members))
        return username

    def category_of(self, username: str) -> str:
        if username.endswith("@chatroom"):
            return "group"
        if username.startswith("gh_"):
            return "official"
        if username in ("filehelper", "fmessage", "medianote", "newsapp", "weixin", "qqmail"):
            return "system"
        return "direct"

    def sessions(self, limit: int = 200) -> list[dict]:
        with self._lock:
            return self._sessions_locked(limit)

    def _sessions_locked(self, limit: int) -> list[dict]:
        con = self._open("session/session.db")
        if con is None or "SessionTable" not in tables_of(con):
            return []
        out = []
        for r in con.execute("select * from SessionTable"):
            d = dict(r)
            username = d.get("username") or ""
            if not username:
                continue
            out.append({
                "username": username,
                "display": self.name_of(username),
                "category": self.category_of(username),
                "unread": d.get("unread_count") or 0,
                "summary": to_text(d.get("summary"))[:160],
                "lastAt": d.get("last_timestamp") or 0,
                "sortAt": d.get("sort_timestamp") or d.get("last_timestamp") or 0,
                "lastSender": self.name_of(d.get("last_msg_sender") or "") if d.get("last_msg_sender") else "",
                "lastType": normalize_type(d.get("last_msg_type")),
            })
        out.sort(key=lambda x: -x["sortAt"])
        return out[:limit]

    def messages(self, username: str, limit: int = 60, start: int | None = None, end: int | None = None) -> list[dict]:
        with self._lock:
            return self._messages_locked(username, limit, start, end)

    def messages_page(self, username: str, start: int, end: int, offset: int = 0, limit: int = 30) -> list[dict]:
        """Stable oldest-first page within the connected, read-only snapshot."""
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("消息分页参数无效")
        with self._lock:
            return self._messages_locked(username, offset + limit, start, end, oldest=True)[offset:offset + limit]

    def _messages_locked(self, username: str, limit: int, start: int | None = None, end: int | None = None, oldest: bool = False) -> list[dict]:
        table = msg_table_for(username)
        rows: list[dict] = []
        for shard, con in enumerate(self._msg_dbs):
            if table not in tables_of(con):
                continue
            id2name = {}
            try:
                id2name = {r[0]: r[1] for r in con.execute("select rowid,user_name from Name2Id")}
            except sqlite3.Error:
                pass
            try:
                cursor = con.execute(
                    'select local_id,server_id,local_type,create_time,real_sender_id,'
                    'message_content,compress_content from "%s" where create_time >= ? and create_time < ? order by create_time %s, local_id %s limit ?' % (table, "asc" if oldest else "desc", "asc" if oldest else "desc"),
                    (start or 0, end if end is not None else 99999999999, limit),
                )
            except sqlite3.Error:
                if oldest:
                    raise RuntimeError("读取聊天数据库分页失败，未将该批标记为完成") from None
                continue
            for r in cursor:
                sender_id = id2name.get(r["real_sender_id"], "")
                mtype = normalize_type(r["local_type"])
                content = decode_content(r["message_content"], r["compress_content"])
                # 群聊正文开头是发送者 wxid，确认是已知身份才剥离
                if username.endswith("@chatroom") and "\n" in content[:256]:
                    head, rest = content.split("\n", 1)
                    head = head.rstrip("\r")
                    if head.endswith(":"):
                        maybe = head[:-1]
                        if maybe == sender_id or maybe in self._contacts or maybe in id2name.values():
                            content = rest
                rows.append({
                    "_shard": shard,
                    "localId": r["local_id"],
                    "serverId": str(r["server_id"] or ""),
                    "type": mtype,
                    "typeName": TYPE_NAMES.get(mtype, "其他"),
                    "at": r["create_time"] or 0,
                    "timeText": datetime.fromtimestamp(r["create_time"]).strftime("%Y-%m-%d %H:%M:%S") if r["create_time"] else "",
                    "sender": self.name_of(sender_id) if sender_id else "",
                    "senderId": sender_id,
                    "text": humanize(content, mtype),
                })
        rows.sort(key=lambda m: (m["at"], m["localId"] or 0, m["_shard"]))
        for row in rows:
            row.pop("_shard", None)
        return (rows[:limit] if oldest else rows[-limit:]) if limit > 0 else []

    def close(self):
        for con in self._conns.values():
            if con is not None:
                try:
                    con.close()
                except sqlite3.Error:
                    pass
