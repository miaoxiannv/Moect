# -*- coding: utf-8 -*-
"""给界面用的本地只读 API。

只监听 127.0.0.1，只暴露读取能力：状态、连接（取密钥 + 解密快照）、
会话列表、某个会话的消息。不做发送、不加好友、不写回微信。
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import agent
import insights
import key
import reader
import secrets_store
import snapshot
import scope_policy
import cipher
import analysis_checkpoint
import automation_store
import auto_startup
import sys
import errno
import socket
import hashlib
import tasks_store

HOST = "127.0.0.1"
PORT = int(os.environ.get("LIUBAI_PORT", "8791"))
CACHE_ROOT = os.environ.get("LIUBAI_CACHE", os.path.join(os.environ.get("USERPROFILE", "."), ".liubai", "snapshots"))

STATE = {
    "phase": "idle",      # idle | connecting | ready | failed
    "message": "",
    "account": None,
    "snapshot": None,
    "startedAt": 0,
    "error": None,
    "stats": {},
}
READER: reader.WxReader | None = None
LOCK = threading.Lock()
JOB_LOCK = threading.Lock()
JOB_THREAD = None
JOB_CANCEL = threading.Event()
AUTO_LOCK = threading.Lock()
AUTO_INTERVAL = 3600
AUTO_OVERLAP = 3600
AUTO_REFRESH = None


def _wxid_of(dirname: str) -> str:
    base = dirname.rsplit("_", 1)[-1]
    return dirname[:-(len(base) + 1)] if len(base) == 4 else dirname


def detect_accounts() -> list[dict]:
    """扫描微信 4.x 的数据根目录，列出有 db_storage 的账号。"""
    roots = []
    appdata = os.environ.get("APPDATA", "")
    cfg_dir = os.path.join(appdata, "Tencent", "xwechat", "config")
    if os.path.isdir(cfg_dir):
        for name in os.listdir(cfg_dir):
            if not name.endswith(".ini"):
                continue
            try:
                raw = open(os.path.join(cfg_dir, name), encoding="utf-8", errors="ignore").read().strip()
            except OSError:
                continue
            if not raw:
                continue
            base = os.path.expandvars(raw)
            if base in ("MyDocument:", "MyDocument"):
                base = os.path.join(os.environ.get("USERPROFILE", "."), "Documents")
            roots.append(os.path.join(base, "xwechat_files"))
    roots.append(os.path.join(os.environ.get("USERPROFILE", "."), "Documents", "xwechat_files"))

    accounts = []
    seen = set()
    for root in roots:
        if not os.path.isdir(root) or root in seen:
            continue
        seen.add(root)
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            storage = os.path.join(path, "db_storage")
            if not os.path.isdir(storage):
                continue
            accounts.append({
                "dirName": name,
                "wxid": _wxid_of(name),
                "path": path,
                "dbStorage": storage,
                "dbCount": sum(1 for _r, _d, fs in os.walk(storage) for f in fs if f.endswith(".db")),
            })
    return accounts


def _missing_core_keys(storage: str, keys: dict) -> list[str]:
    """Validate keys against current DB headers, not just a cache fingerprint."""
    missing = []
    for rel in snapshot.DEFAULT_INCLUDE:
        path = os.path.join(storage, rel.replace("/", os.sep))
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                page = f.read(cipher.PAGE_SZ)
            key_hex = keys.get(page[:cipher.SALT_SZ].hex(), "")
            valid = len(page) == cipher.PAGE_SZ and cipher.verify_enc_key(bytes.fromhex(key_hex), page)
        except (OSError, ValueError, TypeError):
            valid = False
        if not valid:
            missing.append(rel)
    return missing



def _restore_incomplete_snapshot(account, directory):
    """Reopen the exact local snapshot before any sync so a failed job can resume."""
    global READER
    if JOB_THREAD is not None and JOB_THREAD.is_alive():
        return False
    if not os.path.isfile(os.path.join(directory, 'manifest.json')):
        return False
    job = analysis_checkpoint.load(directory, secrets_store.unprotect)
    if not job or job.get('status') not in ('running', 'failed', 'interrupted', 'paused'):
        return False
    selected = scope_policy.load_scope(directory, secrets_store.unprotect)
    config = agent.load_config(CACHE_ROOT)
    if (not config or not job.get('snapshotSignature')
            or job.get('snapshotSignature') != _snapshot_signature(directory)
            or job.get('configSignature') != _job_signature(config)
            or set(job.get('selected', [])) != selected):
        return False
    current = reader.WxReader(directory)
    try:
        sessions = current.sessions(limit=10000)
    except Exception:
        current.close()
        raise
    with LOCK:
        old = READER
        READER = current
        STATE.update(phase='ready', message='已恢复未完成分析的快照', account=account,
                     snapshot=directory, error=None,
                     stats={'sessions':len(sessions), 'restoredCheckpoint':True})
    if old is not None and old is not current:
        old.close()
    return True

def _connect_worker(account: dict):
    """密钥（优先用缓存）→ 增量同步快照 → 建立只读句柄。全程只读。"""
    global READER
    storage = account["dbStorage"]
    out_dir = os.path.join(CACHE_ROOT, account["dirName"])
    started = time.time()
    try:
        if _restore_incomplete_snapshot(account, out_dir):
            return
        fingerprint = secrets_store.account_fingerprint(storage)
        with LOCK:
            STATE.update(phase="connecting", message="正在获取数据库密钥…", error=None, startedAt=started)

        keys = secrets_store.load_keys(out_dir, fingerprint) or {}
        key_source = "cache"
        missing = _missing_core_keys(storage, keys)
        if not keys or missing:
            key_source = "scan"
            with LOCK:
                STATE["message"] = "正在读取并校验微信数据库密钥…"
            diagnostics = {}
            keys.update(key.extract(storage, verbose=False, diagnostics=diagnostics))
            missing = _missing_core_keys(storage, keys)
            if not keys or missing:
                if diagnostics.get("processesOpened") == 0 and diagnostics.get("accessDenied", 0):
                    raise RuntimeError("无法读取微信进程。请确认留白与微信以相同的 Windows 权限运行后重试。")
                if missing:
                    raise RuntimeError("未取得完整的聊天数据库密钥。请确认所选账号是当前已登录的微信，再重试。缺少：" + "、".join(missing))
                raise RuntimeError(
                    "未能找到与所选账号匹配的密钥。请确认所选账号已在电脑微信中登录；"
                    "若仍失败，可能需要适配当前微信版本。"
                )
            secrets_store.save_keys(out_dir, keys, fingerprint)

        with LOCK:
            STATE["message"] = "正在同步数据库快照…"
        os.makedirs(out_dir, exist_ok=True)
        summary = snapshot.sync_databases(storage, out_dir, keys)
        if summary["failed"] and not summary["skipped"] and not summary["incremental"] and not summary["full"]:
            raise RuntimeError("数据库解密全部失败，密钥可能不属于这个账号。")

        old = READER
        READER = reader.WxReader(out_dir)
        if old is not None:
            old.close()

        sessions = READER.sessions(limit=10000)
        with LOCK:
            STATE.update(
                phase="ready",
                message="已连接",
                account=account,
                snapshot=out_dir,
                error=None,
                stats={
                    "keySource": key_source,
                    "databases": summary["total"],
                    "decrypted": summary["total"] - summary["failed"],
                    "keys": len(keys),
                    "skipped": summary["skipped"],
                    "incremental": summary["incremental"],
                    "full": summary["full"],
                    "sessions": len(sessions),
                    "seconds": round(time.time() - started, 1),
                },
            )
    except Exception as exc:  # noqa: BLE001
        with LOCK:
            STATE.update(phase="failed", message="连接失败", error=str(exc))


def start_connect(account: dict) -> bool:
    with LOCK:
        if STATE["phase"] == "connecting":
            return False
        STATE.update(phase="connecting", message="正在准备连接…", error=None)
    threading.Thread(target=_connect_worker, args=(account,), daemon=True).start()
    return True


def handle_status() -> dict:
    with LOCK:
        snapshot_state = dict(STATE)
    import winmem
    procs = winmem.weixin_processes()
    account = snapshot_state.get("account") or {}
    if snapshot_state.get("phase") == "ready" and READER is not None:
        snapshot_state["profile"] = {"name": READER.name_of(account.get("wxid") or ""), "avatarAvailable": False}
    return {
        "wechatRunning": bool(procs),
        "processes": len(procs),
        "accounts": detect_accounts(),
        "state": snapshot_state,
        "cacheRoot": CACHE_ROOT,
    }


def handle_sessions(limit: int) -> dict:
    r = READER
    if r is None:
        return {"sessions": [], "ready": False, "debug": {"reader": None}}
    items = r.sessions(limit=limit)
    _, directory, _ = _scope_context()
    allowed = scope_policy.load_scope(directory, secrets_store.unprotect) if directory else set()
    for item in items:
        if item["username"] not in allowed:
            item["summary"] = ""
            item["lastSender"] = ""
    return {
        "sessions": items,
        "ready": True,
        "debug": {"root": r.root, "count": len(items), "limit": limit, "msgDbs": len(r._msg_dbs)},
    }


def _scope_context():
    with LOCK:
        r = READER
        directory = STATE.get("snapshot")
        account = STATE.get("account") or {}
        phase = STATE.get("phase")
        if r is None or not directory or phase != "ready":
            return None, None, None
        return r, directory, account.get("dirName")


def handle_scope() -> dict:
    r, directory, account = _scope_context()
    if r is None:
        return {"ready": False, "sessions": []}
    allowed = scope_policy.load_scope(directory, secrets_store.unprotect)
    # Only metadata is returned to the picker, not message previews.
    sessions = [{"username": x["username"], "display": x["display"],
                 "category": x["category"], "selected": x["username"] in allowed}
                for x in r.sessions(limit=10000)]
    return {"ready": True, "account": account, "sessions": sessions, "selectedCount": sum(x["selected"] for x in sessions)}


def save_scope(body: dict) -> dict:
    selected = body.get("selected")
    if not isinstance(selected, list) or any(not isinstance(x, str) for x in selected):
        raise ValueError("会话选择格式无效")
    r, directory, account = _scope_context()
    if r is None:
        raise ValueError("请先连接微信")
    if body.get("account") != account:
        raise ValueError("账号已变化，请重新加载会话列表")
    existing = {x["username"] for x in r.sessions(limit=10000)}
    if not set(selected).issubset(existing):
        raise ValueError("会话列表已变化，请重新加载后保存")
    with LOCK:
        if READER is not r or STATE.get("snapshot") != directory or STATE.get("phase") != "ready":
            raise ValueError("账号已变化，请重新加载会话列表")
        scope_policy.save_scope(directory, set(selected), secrets_store.protect)
    return {"ok": True, "selectedCount": len(set(selected))}


def scoped_reader():
    r, directory, _ = _scope_context()
    if r is None:
        return None
    return scope_policy.ScopedReader(r, scope_policy.load_scope(directory, secrets_store.unprotect))


def handle_agent_config() -> dict:
    cfg = agent.load_config(CACHE_ROOT)
    if not cfg:
        return {"configured": False}
    return {
        "configured": True,
        "baseUrl": cfg.get("baseUrl", ""),
        "model": cfg.get("model", ""),
        "keyMasked": agent.mask(cfg.get("apiKey", "")),
        "hasKey": bool(cfg.get("apiKey")),
    }


def handle_agent_save(body: dict) -> dict:
    base_url = (body.get("baseUrl") or "").strip()
    model = (body.get("model") or "").strip()
    api_key = body.get("apiKey")
    if not base_url or not model:
        return {"ok": False, "error": "API 地址和模型名不能为空"}
    existing = agent.load_config(CACHE_ROOT) or {}
    if api_key is None or api_key == "":
        api_key = existing.get("apiKey", "")
    if not agent.save_config(CACHE_ROOT, base_url, model, api_key):
        return {"ok": False, "error": "配置保存失败"}
    cfg = agent.load_config(CACHE_ROOT) or {}
    test = agent.test_connection(cfg)
    return {"ok": True, "saved": True, "test": test,
            "keyMasked": agent.mask(cfg.get("apiKey", ""))}


def handle_analyze(days: int, expected_account=None, expected_selected=None, expected_config=None, start=None, end=None) -> dict:
    """优先用用户配置的模型归纳；没有配置或调用失败则回退规则引擎。"""
    with LOCK:
        current_reader = READER
        directory = STATE.get("snapshot")
        account = STATE.get("account") or {}
        if current_reader is None or not directory or STATE.get("phase") != "ready":
            return {"ready": False, "insights": [], "source": "none", "error": "还没有连接微信"}
        selected = scope_policy.load_scope(directory, secrets_store.unprotect)
        if expected_account is not None and (account.get("dirName") != expected_account or selected != set(expected_selected)):
            raise ValueError("账号或已保存范围已变化，请重新加载后再分析")
    self_wxid = account.get("wxid") or ""
    my_name = current_reader.name_of(self_wxid) if self_wxid else ""

    allowed_reader = scope_policy.ScopedReader(current_reader, selected)
    cfg = agent.load_config(CACHE_ROOT)
    if expected_config is not None and (not cfg or cfg.get("baseUrl") != expected_config.get("baseUrl") or cfg.get("model") != expected_config.get("model")):
        raise ValueError("模型服务配置已变化，请重新加载后再分析")
    if cfg:
        result = agent.analyze(allowed_reader, self_wxid, cfg, days, start, end)
        if result.get("ok"):
            with LOCK:
                current_account = STATE.get("account") or {}
                if (STATE.get("phase") != "ready" or STATE.get("snapshot") != directory
                        or current_account.get("dirName") != account.get("dirName")
                        or scope_policy.load_scope(directory, secrets_store.unprotect) != selected
                        or agent.load_config(CACHE_ROOT) != cfg):
                    raise ValueError("分析期间账号、会话范围或模型配置已更改，结果未保存，请重试")
                result["tasksAdded"] = tasks_store.merge_analysis(
                    directory, result.get("insights", []), secrets_store.protect, secrets_store.unprotect)
            result.update({"ready": True, "selfWxid": self_wxid, "selfName": my_name,
                           "day": __import__("datetime").datetime.now().strftime("%Y-%m-%d")})
            return result
        fallback = insights.build_digest(allowed_reader, self_wxid, my_name, recent_days=days, start=start, end=end)
        fallback.update({"ready": True, "source": "rule", "selfWxid": self_wxid,
                         "selfName": my_name, "agentError": result.get("error")})
        return fallback

    result = insights.build_digest(allowed_reader, self_wxid, my_name, recent_days=days, start=start, end=end)
    result.update({"ready": True, "source": "rule", "selfWxid": self_wxid, "selfName": my_name})
    return result



BATCH_SIZE = 24
BATCH_OVERLAP = 3

def _job_signature(cfg):
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()

def _snapshot_signature(directory):
    try:
        with open(os.path.join(directory, 'manifest.json'), 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        return ''

def _job_public(job):
    if not job:
        return {'status': 'none'}
    return {k: job.get(k) for k in ('id','status','error','sessionIndex','sessionCount',
            'messagesProcessed','batchesCompleted','tasksAdded','insights','startDate','endDate','kind','stage','summaryIndex','summaryCount','resumeNote')}

def get_analysis_job():
    with LOCK:
        directory = STATE.get('snapshot')
        if not directory or STATE.get('phase') != 'ready':
            return {'status': 'none'}
        job = analysis_checkpoint.load(directory, secrets_store.unprotect)
        if not job:
            return {'status': 'none'}
        if set(job.get('selected', [])) != scope_policy.load_scope(directory, secrets_store.unprotect):
            return {'status': 'none'}
        result = _job_public(job)
    if result['status'] == 'running' and (JOB_THREAD is None or not JOB_THREAD.is_alive()):
        result['status'] = 'interrupted'
    return result

def start_analysis_job(body, internal_window=None):
    from datetime import datetime, timedelta
    global JOB_THREAD
    start_date = datetime.strptime(body.get('startDate', ''), '%Y-%m-%d')
    end_date = datetime.strptime(body.get('endDate', ''), '%Y-%m-%d')
    if end_date < start_date or (end_date - start_date).days > 365:
        raise ValueError('日期范围无效（最多 366 天）')
    start_ts = int(start_date.timestamp()) if internal_window is None else int(internal_window[0])
    end_ts = int((end_date + timedelta(days=1)).timestamp()) if internal_window is None else int(internal_window[1])
    if start_ts >= end_ts:
        raise ValueError('分析时间范围无效')
    with JOB_LOCK:
        if JOB_THREAD is not None and JOB_THREAD.is_alive():
            raise ValueError('已有分析正在进行')
        with LOCK:
            directory, r, account = STATE.get('snapshot'), READER, STATE.get('account') or {}
            if not directory or r is None or STATE.get('phase') != 'ready':
                raise ValueError('请先连接微信')
            selected = scope_policy.load_scope(directory, secrets_store.unprotect)
            if body.get('account') != account.get('dirName') or set(body.get('selected', [])) != selected:
                raise ValueError('账号或会话范围已变化，请刷新后重试')
            if not selected:
                raise ValueError('请先选择会话')
            cfg = agent.load_config(CACHE_ROOT)
            config = body.get('config') or {}
            if not cfg or cfg.get('baseUrl') != config.get('baseUrl') or cfg.get('model') != config.get('model'):
                raise ValueError('模型配置已变化，请刷新后重试')
            signature = _job_signature(cfg)
            snapshot_sig = _snapshot_signature(directory)
            old = analysis_checkpoint.load(directory, secrets_store.unprotect)
            same_request = (old and old.get('status') in ('running','failed','interrupted','paused')
                    and old.get('startDate') == body['startDate'] and old.get('endDate') == body['endDate']
                    and set(old.get('selected', [])) == selected and old.get('configSignature') == signature
                    and old.get('start') == start_ts and old.get('end') == end_ts
                    and old.get('kind', 'manual') == ('auto' if internal_window is not None else 'manual'))
            if same_request:
                job = old
                if job.get('snapshotSignature') != snapshot_sig:
                    # Offset positions are unsafe after a snapshot change. Recheck
                    # the current conversation from its beginning; persisted evidence
                    # and completed conversations are kept and de-duplicated.
                    offset = job.get('offset', 0)
                    if job.get('sessionIndex', 0) < len(job.get('sessions', [])):
                        job['messagesProcessed'] = max(0, job.get('messagesProcessed', 0) - (offset + BATCH_OVERLAP if offset else 0))
                        job['offset'] = 0
                    job['snapshotSignature'] = snapshot_sig
                    job['resumeNote'] = '微信快照已更新，正在重新核查当前会话；已保存事项保留。'
            else:
                sessions = scope_policy.ScopedReader(r, selected).sessions(limit=10000)
                sessions = [s for s in sessions if s.get('category') != 'official']
                job = {'version':1, 'id':__import__('uuid').uuid4().hex, 'status':'running',
                       'selected':sorted(selected), 'configSignature':signature, 'snapshotSignature':snapshot_sig,
                       'startDate':body['startDate'], 'endDate':body['endDate'],
                       'start':start_ts, 'end':end_ts, 'kind':'auto' if internal_window is not None else 'manual',
                       'sessions':[{'username':s['username'],'display':s['display'], 'category':s['category'],
                                    'sortAt':s.get('sortAt') or 0} for s in sessions],
                       'sessionIndex':0, 'sessionCount':len(sessions), 'offset':0,
                       'messagesProcessed':0, 'batchesCompleted':0, 'tasksAdded':0, 'batchSize':BATCH_SIZE, 'insights':[], 'stage':'messages'}
            job['status'], job['error'] = 'running', ''
            analysis_checkpoint.save(directory, job, secrets_store.protect)
        JOB_CANCEL.clear()
        JOB_THREAD = threading.Thread(target=_analysis_job_worker, args=(directory, r, account.copy(), cfg, job), daemon=True)
        JOB_THREAD.start()
        return _job_public(job)

def pause_analysis_job(body):
    with JOB_LOCK:
        with LOCK:
            account = STATE.get('account') or {}
            if STATE.get('phase') != 'ready' or body.get('account') != account.get('dirName'):
                raise ValueError('账号已变化，请刷新后重试')
        if JOB_THREAD is None or not JOB_THREAD.is_alive():
            return {'requested': False}
        JOB_CANCEL.set()
        return {'requested': True}

def _analysis_job_worker(directory, current_reader, account, cfg, job):
    selected = set(job['selected'])
    scoped = scope_policy.ScopedReader(current_reader, selected)
    self_wxid = account.get('wxid') or ''
    my_name = current_reader.name_of(self_wxid) if self_wxid else ''
    def verify():
        if (READER is not current_reader or STATE.get('phase') != 'ready' or STATE.get('snapshot') != directory
                or (STATE.get('account') or {}).get('dirName') != account.get('dirName')
                or scope_policy.load_scope(directory, secrets_store.unprotect) != selected
                or agent.load_config(CACHE_ROOT) != cfg or _snapshot_signature(directory) != job['snapshotSignature']):
            raise ValueError('账号、范围、模型或快照已变化；请重新开始分析')
    try:
        while job['sessionIndex'] < len(job['sessions']):
            if JOB_CANCEL.is_set():
                with LOCK:
                    verify()
                    job['status'] = 'paused'
                    analysis_checkpoint.save(directory, job, secrets_store.protect)
                return
            with LOCK:
                verify()
                session = job['sessions'][job['sessionIndex']]
                batch_size = job.get('batchSize', BATCH_SIZE)
                messages = scoped.messages_page(session['username'], job['start'], job['end'], job['offset'], batch_size)
                known = [t for t in tasks_store.load(directory, secrets_store.unprotect)
                         if t.get('conversationId') == session['username']]
            if messages:
                class BatchReader:
                    def sessions(self, limit=10000):
                        return [dict(session, sortAt=max(session['sortAt'], job['start']))]
                    def messages(self, username, limit=60, start=None, end=None):
                        return messages
                result = agent.analyze(BatchReader(), self_wxid, cfg, 1, job['start'], job['end'],
                                       known_tasks=known, per_session_limit=batch_size, user_name=my_name)
                if not result.get('ok'):
                    reason = str(result.get('error') or '未知错误')
                    if batch_size > 6 and ('超时' in reason or '长度限制' in reason or '有效 JSON' in reason):
                        with LOCK:
                            verify()
                            job['batchSize'] = max(6, batch_size // 2)
                            analysis_checkpoint.save(directory, job, secrets_store.protect)
                        continue
                    raise RuntimeError('模型批次未完成：' + reason[:180])
            else:
                result = {'insights': []}
            with LOCK:
                verify()
                job['tasksAdded'] += tasks_store.merge_analysis(directory, result['insights'], secrets_store.protect, secrets_store.unprotect)
                automation_store.merge_daily(directory, result['insights'], secrets_store.protect, secrets_store.unprotect)
                existing = {(x.get('category'), (x.get('sources') or [{}])[0].get('conversationId'),
                             (x.get('sources') or [{}])[0].get('serverId'), x.get('title'))
                            for x in job['insights']}
                for item in result['insights']:
                    if item.get('category') == 'task_update':
                        continue
                    ref = (item.get('sources') or [{}])[0]
                    key = (item.get('category'), ref.get('conversationId'), ref.get('serverId'), item.get('title'))
                    if key not in existing:
                        job['insights'].append(item)
                        existing.add(key)
                job['batchesCompleted'] += int(bool(messages))
                job['messagesProcessed'] += max(0, len(messages) - (BATCH_OVERLAP if job['offset'] else 0))
                if len(messages) < batch_size:
                    job['sessionIndex'] += 1
                    job['offset'] = 0
                else:
                    job['offset'] += batch_size - BATCH_OVERLAP
                # Empty sessions do not send anything or mutate tasks; a crash may
                # safely recheck them, avoiding hundreds of redundant file swaps.
                if messages or job['sessionIndex'] % 25 == 0:
                    analysis_checkpoint.save(directory, job, secrets_store.protect)
        from datetime import datetime
        if 'summaryDates' not in job:
            dates = {datetime.fromtimestamp(ref['at']).strftime('%Y-%m-%d')
                     for item in job['insights'] for ref in (item.get('sources') or [])[:1] if ref.get('at')}
            job['summaryDates'], job['summaryIndex'] = sorted(dates), 0
            job['summaryCount'], job['stage'] = len(dates), 'summarizing'
            with LOCK:
                verify()
                analysis_checkpoint.save(directory, job, secrets_store.protect)
        while job['summaryIndex'] < len(job['summaryDates']):
            if JOB_CANCEL.is_set():
                with LOCK:
                    verify()
                    job['status'] = 'paused'
                    analysis_checkpoint.save(directory, job, secrets_store.protect)
                return
            date = job['summaryDates'][job['summaryIndex']]
            with LOCK:
                verify()
                daily_items = automation_store.read_daily(directory, date, selected, secrets_store.unprotect)
            summary_limit = job.get('summaryLimit', 60)
            summary = agent.summarize_daily(daily_items, cfg, date, max_items=summary_limit)
            if not summary.get('ok'):
                reason = str(summary.get('error') or '每日总结未完成')
                if summary_limit > 10 and any(term in reason for term in ('超时', '长度限制', '有效 JSON')):
                    with LOCK:
                        verify()
                        job['summaryLimit'] = max(10, summary_limit // 2)
                        analysis_checkpoint.save(directory, job, secrets_store.protect)
                    continue
                raise RuntimeError(reason)
            with LOCK:
                verify()
                summary['generatedAt'] = int(time.time())
                summary['scopeSignature'] = _scope_signature(selected)
                automation_store.save_summary(directory, date, summary, secrets_store.protect)
                job['summaryIndex'] += 1
                analysis_checkpoint.save(directory, job, secrets_store.protect)
        with LOCK:
            verify()
            job['stage'], job['status'] = 'done', 'complete'
            analysis_checkpoint.save(directory, job, secrets_store.protect)
    except Exception as exc:
        with LOCK:
            job['status'], job['error'] = 'failed', str(exc)[:250]
            try:
                analysis_checkpoint.save(directory, job, secrets_store.protect)
            except Exception:
                pass


AUTO_NAME = 'automation.bin'
AUTO_INDEX = 'automation-active.bin'

def _auto_directory(account_id):
    if not isinstance(account_id, str) or not account_id or os.path.basename(account_id) != account_id or account_id in ('.', '..'):
        raise ValueError('自动分析账号无效')
    return os.path.join(CACHE_ROOT, account_id)

def _scope_signature(selected):
    return hashlib.sha256(json.dumps(sorted(selected), ensure_ascii=False).encode('utf-8')).hexdigest()

def get_automation():
    with LOCK:
        directory = STATE.get('snapshot')
        if not directory or STATE.get('phase') != 'ready':
            return {'enabled': False, 'state': 'disconnected'}
        cfg = automation_store.load(directory, AUTO_NAME, secrets_store.unprotect, {})
        index = automation_store.load(CACHE_ROOT, AUTO_INDEX, secrets_store.unprotect, {})
        if cfg.get('enabled') and index.get('account') != (STATE.get('account') or {}).get('dirName'):
            cfg = dict(cfg, enabled=False, state='inactive', error='当前自动分析已切换到其他账号')
    return {k:cfg.get(k) for k in ('enabled','state','error','nextRunAt','lastSuccessEnd','startupError')}

def set_automation(body):
    enabled = body.get('enabled')
    if not isinstance(enabled, bool):
        raise ValueError('自动分析设置无效')
    with AUTO_LOCK:
        with LOCK:
            directory = STATE.get('snapshot')
            account = (STATE.get('account') or {}).copy()
            if not directory or STATE.get('phase') != 'ready' or account.get('dirName') != body.get('account'):
                raise ValueError('账号未连接或已切换')
            selected = scope_policy.load_scope(directory, secrets_store.unprotect)
            model = agent.load_config(CACHE_ROOT)
            if enabled and not selected: raise ValueError('请先选择会话范围')
            if enabled and not model: raise ValueError('请先配置模型服务')
            cfg = automation_store.load(directory, AUTO_NAME, secrets_store.unprotect, {})
            index = automation_store.load(CACHE_ROOT, AUTO_INDEX, secrets_store.unprotect, {})
            if not enabled and JOB_THREAD is not None and JOB_THREAD.is_alive():
                active_job = analysis_checkpoint.load(directory, secrets_store.unprotect)
                if active_job and active_job.get('kind') == 'auto': JOB_CANCEL.set()
        startup_error = ''
        if enabled or index.get('account') == account['dirName']:
            try:
                auto_startup.configure(enabled)
            except Exception as exc:
                startup_error = str(exc)[:160]
        with LOCK:
            if (STATE.get('snapshot') != directory or STATE.get('phase') != 'ready'
                    or (STATE.get('account') or {}).get('dirName') != account['dirName']
                    or scope_policy.load_scope(directory, secrets_store.unprotect) != selected
                    or (enabled and agent.load_config(CACHE_ROOT) != model)):
                raise ValueError('账号、会话范围或模型已变化，请重新操作')
            cfg['enabled'], cfg['startupError'] = enabled, startup_error
            cfg['state'], cfg['error'] = ('waiting' if enabled else 'disabled'), ''
            if enabled:
                cfg['nextRunAt'] = int(time.time())
                pending = cfg.get('pending')
                if pending and (pending.get('scopeSignature') != _scope_signature(selected)
                                or pending.get('configSignature') != _job_signature(model)):
                    cfg['pending'], cfg['scopeSignature'], cfg['lastSuccessEnd'] = None, '', None
                old = analysis_checkpoint.load(directory, secrets_store.unprotect)
                if cfg.get('pending') and old and old.get('id') == cfg['pending'].get('jobId') and old.get('status') == 'paused':
                    old['status'] = 'interrupted'
                    analysis_checkpoint.save(directory, old, secrets_store.protect)
            automation_store.save(directory, AUTO_NAME, cfg, secrets_store.protect)
            if enabled:
                automation_store.save(CACHE_ROOT, AUTO_INDEX, {'account':account['dirName']}, secrets_store.protect)
            elif JOB_THREAD is not None and JOB_THREAD.is_alive():
                job = analysis_checkpoint.load(directory, secrets_store.unprotect)
                if job and job.get('kind') == 'auto': JOB_CANCEL.set()
            return {k:cfg.get(k) for k in ('enabled','state','error','nextRunAt','lastSuccessEnd','startupError')}

def get_daily(date):
    with LOCK:
        directory = STATE.get('snapshot')
        if not directory or STATE.get('phase') != 'ready':
            raise ValueError('请先连接微信')
        allowed = scope_policy.load_scope(directory, secrets_store.unprotect)
        values = automation_store.read_daily(directory, date, allowed, secrets_store.unprotect)
        summary = automation_store.read_summary(directory, date, allowed, secrets_store.unprotect)
        if summary and summary.get('scopeSignature') != _scope_signature(allowed):
            summary = None
    return {'date':date, 'insights':values, 'summary':summary}

def _automation_tick(now=None):
    """One scheduler transition. Time watermarks advance only on completed jobs."""
    global AUTO_REFRESH
    now = int(time.time() if now is None else now)
    with AUTO_LOCK:
        index = automation_store.load(CACHE_ROOT, AUTO_INDEX, secrets_store.unprotect, {})
        account_id = index.get('account')
        if not account_id:
            return
        directory = _auto_directory(account_id)
        cfg = automation_store.load(directory, AUTO_NAME, secrets_store.unprotect, {})
        if not cfg.get('enabled'):
            return
        def save_auto():
            automation_store.save(directory, AUTO_NAME, cfg, secrets_store.protect)
        with LOCK:
            phase, active_account = STATE.get('phase'), (STATE.get('account') or {}).copy()
        if JOB_THREAD is not None and JOB_THREAD.is_alive():
            return
        if phase == 'connecting':
            return
        if phase != 'ready':
            if now < (cfg.get('nextRunAt') or 0): return
            import winmem
            if not winmem.weixin_processes():
                cfg.update(state='waiting_wechat', error='等待电脑微信登录', nextRunAt=now+300);save_auto();return
            acct = next((a for a in detect_accounts() if a['dirName'] == account_id), None)
            if not acct:
                cfg.update(state='waiting_wechat', error='找不到已启用自动分析的微信账号', nextRunAt=now+300);save_auto();return
            cfg.update(state='connecting', error='', nextRunAt=now+300);save_auto()
            start_connect(acct)
            return
        if active_account.get('dirName') != account_id:
            cfg.update(state='waiting_account', error='请切回已启用自动分析的微信账号');save_auto();return
        with LOCK:
            selected = scope_policy.load_scope(directory, secrets_store.unprotect)
            model = agent.load_config(CACHE_ROOT)
            old = analysis_checkpoint.load(directory, secrets_store.unprotect)
        if not selected or not model:
            cfg.update(state='waiting_config', error='请先选择会话并配置模型');save_auto();return
        scope_sig = _scope_signature(selected)
        config_sig = _job_signature(model)
        pending = cfg.get('pending')
        if pending and (pending.get('scopeSignature') != scope_sig or pending.get('configSignature') != config_sig):
            cfg.update(state='waiting_config', error='会话范围或模型已改变；请重新启用自动分析以确认新范围', enabled=False)
            try: auto_startup.configure(False)
            except Exception as exc: cfg['startupError'] = str(exc)[:160]
            save_auto();return
        if pending and old and old.get('id') == pending.get('jobId'):
            if old.get('status') == 'complete':
                cfg.update(lastSuccessEnd=pending['end'], scopeSignature=scope_sig, pending=None,
                           state='waiting', error='', nextRunAt=max(now, pending['end']+AUTO_INTERVAL))
                save_auto();return
            if old.get('status') == 'paused':
                cfg.update(state='paused', error='自动分析已暂停；重新启用后继续', enabled=False)
                try: auto_startup.configure(False)
                except Exception as exc: cfg['startupError'] = str(exc)[:160]
                save_auto();return
            if old.get('status') == 'failed' and cfg.get('state') == 'running':
                cfg.update(state='retry_wait', error=old.get('error') or '分析失败', nextRunAt=now+AUTO_INTERVAL)
                save_auto();return
        if now < (cfg.get('nextRunAt') or 0): return
        if old and old.get('kind') != 'auto' and old.get('status') in ('failed','interrupted','paused','running'):
            cfg.update(state='waiting_manual', error='请先继续或完成手动分析');save_auto();return
        if pending:
            from datetime import datetime
            body = {'account':account_id, 'selected':sorted(selected),
                    'config':{'baseUrl':model['baseUrl'],'model':model['model']},
                    'startDate':datetime.fromtimestamp(pending['start']).strftime('%Y-%m-%d'),
                    'endDate':datetime.fromtimestamp(pending['end']-1).strftime('%Y-%m-%d')}
            result = start_analysis_job(body, internal_window=(pending['start'],pending['end']))
            pending['jobId'] = result['id']
            cfg.update(pending=pending, state='running', error='');save_auto();return
        if cfg.get('state') == 'syncing':
            with LOCK:
                stats = STATE.get('stats') or {}
            if (stats.get('databases') or 0) > (stats.get('decrypted') or 0):
                cfg.update(state='retry_wait', error='部分数据库同步失败，未推进分析进度', nextRunAt=now+AUTO_INTERVAL)
                save_auto();return
            if AUTO_REFRESH != cfg.get('syncStart'):
                cfg.update(state='waiting', error='', nextRunAt=now);save_auto();return
            from datetime import datetime
            end = cfg['syncStart']
            if cfg.get('scopeSignature') != scope_sig:
                start = int(datetime.fromtimestamp(end).replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
            else:
                start = max(0, int(cfg.get('lastSuccessEnd') or end)-AUTO_OVERLAP)
            if start >= end:
                cfg.update(state='waiting', nextRunAt=now+AUTO_INTERVAL);save_auto();return
            cfg['pending']={'start':start,'end':end,'scopeSignature':scope_sig,'configSignature':config_sig}
            cfg.update(state='pending', error='', nextRunAt=now);save_auto();return
        # Refresh first. If the service restarts while syncing, the volatile token
        # is lost and a fresh sync is required before advancing a watermark.
        with JOB_LOCK:
            if JOB_THREAD is not None and JOB_THREAD.is_alive():
                return
            cfg.update(state='syncing', syncStart=now, error='');save_auto()
            AUTO_REFRESH = now
            if not start_connect(active_account):
                cfg.update(state='waiting', error='采集服务正忙', nextRunAt=now+60);save_auto()

def _automation_loop():
    while True:
        try:
            _automation_tick()
        except Exception as exc:
            try:
                index = automation_store.load(CACHE_ROOT, AUTO_INDEX, secrets_store.unprotect, {})
                directory = _auto_directory(index.get('account'))
                with AUTO_LOCK:
                    cfg = automation_store.load(directory, AUTO_NAME, secrets_store.unprotect, {})
                    if cfg.get('enabled'):
                        cfg.update(state='retry_wait', error=str(exc)[:200], nextRunAt=int(time.time())+AUTO_INTERVAL)
                        automation_store.save(directory, AUTO_NAME, cfg, secrets_store.protect)
            except Exception:
                pass
        time.sleep(15)

def handle_digest(days: int) -> dict:
    if READER is None:
        return {"insights": [], "ready": False}
    with LOCK:
        account = STATE.get("account") or {}
    self_wxid = account.get("wxid") or ""
    my_name = READER.name_of(self_wxid) if self_wxid else ""
    allowed_reader = scoped_reader()
    if allowed_reader is None:
        return {"insights": [], "ready": False}
    digest = insights.build_digest(allowed_reader, self_wxid, my_name, recent_days=days)
    digest["ready"] = True
    digest["selfWxid"] = self_wxid
    digest["selfName"] = my_name
    return digest


def handle_messages(username: str, limit: int) -> dict:
    if READER is None:
        return {"messages": [], "ready": False}
    allowed_reader = scoped_reader()
    if allowed_reader is None:
        return {"messages": [], "ready": False}
    return {
        "messages": allowed_reader.messages(username, limit=limit),
        "session": username,
        "display": READER.name_of(username),
        "ready": True,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "LiubaiCollector/1.0"

    def _allowed_origin(self):
        origin = self.headers.get("Origin")
        return not origin or origin in {"http://localhost:5183", "http://127.0.0.1:5183",
                                        "tauri://localhost", "http://tauri.localhost"}

    def _send(self, payload: dict, code: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        origin = self.headers.get("Origin")
        if origin and self._allowed_origin():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # 静默访问日志
        return

    def do_OPTIONS(self):
        if not self._allowed_origin():
            self._send({"error": "来源不允许"}, 403)
            return
        self._send({})

    def do_GET(self):
        if not self._allowed_origin():
            self._send({"error": "来源不允许"}, 403)
            return
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/status":
                self._send(handle_status())
            elif parsed.path == "/api/capabilities":
                self._send({"scopeBoundAnalysis": True})
            elif parsed.path == "/api/sessions":
                limit = int(query.get("limit", ["200"])[0])
                self._send(handle_sessions(limit))
            elif parsed.path == "/api/scope":
                self._send(handle_scope())
            elif parsed.path == "/api/tasks":
                with LOCK:
                    directory = STATE.get("snapshot")
                    if not directory or STATE.get("phase") != "ready":
                        raise ValueError("请先连接微信账号")
                    items = tasks_store.load(directory, secrets_store.unprotect)
                self._send({"tasks": tasks_store.visible(items)})
            elif parsed.path == "/api/analysis/job":
                self._send(get_analysis_job())
            elif parsed.path == "/api/automation":
                self._send(get_automation())
            elif parsed.path == "/api/daily":
                self._send(get_daily(query.get("date", [""])[0]))
            elif parsed.path == "/api/agent/config":
                self._send(handle_agent_config())
            elif parsed.path == "/api/analyze":
                days = int(query.get("days", ["5"])[0])
                self._send(handle_analyze(days))
            elif parsed.path == "/api/digest":
                days = int(query.get("days", ["5"])[0])
                self._send(handle_digest(days))
            elif parsed.path == "/api/messages":
                username = query.get("username", [""])[0]
                limit = int(query.get("limit", ["60"])[0])
                if not username:
                    self._send({"error": "缺少 username"}, 400)
                else:
                    self._send(handle_messages(username, limit))
            else:
                self._send({"error": "未知路径"}, 404)
        except PermissionError as exc:
            self._send({"error": str(exc)}, 403)
        except Exception as exc:  # noqa: BLE001
            self._send({"error": str(exc)}, 500)

    def do_POST(self):
        if not self._allowed_origin():
            self._send({"error": "来源不允许"}, 403)
            return
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            body = {}
        try:
            if parsed.path == "/api/agent/config":
                self._send(handle_agent_save(body))
            elif parsed.path == "/api/scope":
                self._send(save_scope(body))
            elif parsed.path == "/api/tasks":
                with LOCK:
                    directory = STATE.get("snapshot")
                    account = STATE.get("account") or {}
                    if not directory or STATE.get("phase") != "ready" or body.get("account") != account.get("dirName"):
                        raise ValueError("账号未连接或已切换，请刷新后重试")
                    items = tasks_store.update(directory, body, secrets_store.protect, secrets_store.unprotect)
                self._send({"tasks": tasks_store.visible(items)})
            elif parsed.path == "/api/automation":
                self._send(set_automation(body))
            elif parsed.path == "/api/analysis/pause":
                self._send(pause_analysis_job(body))
            elif parsed.path == "/api/analysis/job":
                self._send(start_analysis_job(body))
            elif parsed.path == "/api/analyze":
                selected = body.get("selected")
                config = body.get("config")
                if not isinstance(selected, list) or any(not isinstance(x, str) for x in selected) or not isinstance(body.get("account"), str) or not isinstance(config, dict) or not isinstance(config.get("baseUrl"), str) or not isinstance(config.get("model"), str):
                    self._send({"error": "分析范围格式无效"}, 400)
                    return
                from datetime import datetime, timedelta
                start_date = datetime.strptime(body["startDate"], "%Y-%m-%d")
                end_date = datetime.strptime(body["endDate"], "%Y-%m-%d")
                if end_date < start_date or (end_date - start_date).days > 365:
                    raise ValueError("日期范围无效（最多 366 天）")
                start_ts = int(start_date.timestamp())
                end_ts = int((end_date + timedelta(days=1)).timestamp())
                self._send(handle_analyze(5, body["account"], selected, config, start_ts, end_ts))
            elif parsed.path == "/api/agent/test":
                cfg = agent.load_config(CACHE_ROOT)
                if not cfg:
                    self._send({"ok": False, "error": "还没有保存配置"})
                else:
                    self._send(agent.test_connection(cfg))
            elif parsed.path == "/api/connect":
                dir_name = body.get("dirName")
                accounts = detect_accounts()
                target = next((a for a in accounts if a["dirName"] == dir_name), None)
                if target is None and not dir_name and len(accounts) == 1:
                    target = accounts[0]
                if target is None:
                    self._send({"error": "请选择有效的微信账号"}, 400)
                    return
                started = start_connect(target)
                self._send({"started": started, "account": target})
            else:
                self._send({"error": "未知路径"}, 404)
        except Exception as exc:  # noqa: BLE001
            self._send({"error": str(exc)}, 500)



class ExclusiveHTTPServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR can allow two listeners on one address; that makes
    # them race on the same decrypted snapshot and encrypted checkpoints.
    allow_reuse_address = False

    def server_bind(self):
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

def main():
    os.makedirs(CACHE_ROOT, exist_ok=True)
    try:
        server = ExclusiveHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, 10048):
            return
        raise
    # The listener must be acquired first: scheduled duplicate instances must
    # never run another scheduler in parallel with the existing service.
    threading.Thread(target=_automation_loop, name='hourly-agent', daemon=True).start()
    if sys.stdout is not None:
        print("留白采集服务已启动： http://%s:%d" % (HOST, PORT))
        print("缓存目录：", CACHE_ROOT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
