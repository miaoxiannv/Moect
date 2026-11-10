# -*- coding: utf-8 -*-
"""把归纳交给用户自己的模型 API（OpenAI 兼容）。

设计要点：
- 配置（baseUrl / model / apiKey）用 DPAPI 加密存在本机，随用户走。
- 模型只拿到「经过裁剪的会话片段」，每条带编号，便于回指原文。
- 模型输出必须是 JSON 数组，且每条要引用输入里的编号；
  引用了不存在的编号、或没有引用的条目，一律丢弃 —— 不给编造留空间。
- 模型不可用时，调用方回退到规则引擎，界面会标注是哪一种来源。
"""
from __future__ import annotations

import json
import os
import re
import secrets_store
import socket
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlparse

CONFIG_NAME = "agent.bin"
TIMEOUT = 180
MAX_SESSIONS = 30
MAX_MESSAGES_PER_SESSION = 18
MAX_TOTAL_CHARS = 14000

SYSTEM_PROMPT = """你是微信消息分析助手，帮用户从聊天记录里找出真正需要他关注的事。

输入是一段编号的消息列表，每行格式：[编号] 会话名 | 发送者 | 时间 | 正文
「我」代表用户本人。

把事项分成六类：
- task：对话中明确的可执行行动，包括我需要做、等待对方、责任人不确定；不能将别人被点名的任务说成我的任务
- task_update：已有待办后续出现明确完成证据时，建议标记可能已完成；必须引用新消息并填写现有 taskId
- related：直接提到用户、向用户提问、在等用户回复、用户自己答应过的事
- opportunity：对方提出的合作、需求、报价、项目邀约
- group：群聊中重要的观点、行业动态、经验、资源或值得记录的讨论，即使没有直接提到用户
- worth：其他有价值但无需立刻行动的信息

硬性规则：
1. 只输出 JSON 数组，不要任何解释文字、不要 markdown 代码块。
2. 每条必须有 evidence 字段，值是输入里真实存在的编号（1-3 个）。
3. 不许编造输入里没有的信息（价格、日期、姓名、承诺）。
4. 不要因为没有 @ 用户就忽略重要群聊；群聊结论应有具体证据。
5. 原文没有明说的推断，把 confidence 设为 "inferred"；原文直接能读出的设 "direct"。
6. 最多 12 条，按重要程度从高到低排。
7. 标题写具体的人和事，不要写「发现潜在机会」这类空话。
8. 待办必须是具体行动。assignee 只能是 self（我做）、other（对方做）、unknown（无法确认），禁止猜测。
9. dueText 只能逐字摘录证据中明确的时间原文，无法确定则为空；不要将相对时间换算成具体日期。
10. 如果后续消息明确表示已有待办已完成，即使没有新的待办，也必须返回 task_update；taskId 必须逐字使用已有待办里的 ID，evidence 引用完成消息的编号。没有明确证据不能判断完成。
例如已有待办“发送资料”，消息“资料已经发给你”，应输出 {"category":"task_update","taskId":"已有任务ID","evidence":["消息编号"]}。

输出格式（严格遵守）：
[{"category":"task","title":"...","summary":"...","relation":"...","assignee":"self|other|unknown","dueText":"原文时间或空","confidence":"direct","evidence":["12"]},{"category":"task_update","taskId":"已有任务ID","evidence":["45"]}]"""


def config_path(cache_root: str) -> str:
    return os.path.join(cache_root, CONFIG_NAME)


def load_config(cache_root: str) -> dict | None:
    path = config_path(cache_root)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    plain = secrets_store.unprotect(raw)
    if plain is None:
        return None
    try:
        data = json.loads(plain.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if data.get("baseUrl") and data.get("model") else None


def save_config(cache_root: str, base_url: str, model: str, api_key: str) -> bool:
    os.makedirs(cache_root, exist_ok=True)
    payload = json.dumps(
        {"baseUrl": base_url.rstrip("/"), "model": model, "apiKey": api_key}
    ).encode("utf-8")
    sealed = secrets_store.protect(payload)
    if sealed is None:
        return False
    try:
        with open(config_path(cache_root), "wb") as f:
            f.write(sealed)
        return True
    except OSError:
        return False


def mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return key[:3] + "•" * 8 + key[-4:]


def _post_json(url: str, payload: dict, api_key: str, timeout: int = TIMEOUT) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "liubai-workbench/0.1"}
    if (urlparse(url).hostname or "").lower() == "opencode.ai":
        headers["x-opencode-session"] = str(uuid.uuid4())
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def chat_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def test_connection(config: dict) -> dict:
    """发一个最小请求，验证地址、密钥、模型名。"""
    try:
        data = _post_json(
            chat_url(config["baseUrl"]),
            {
                "model": config["model"],
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 32,
                "stream": False,
            },
            config.get("apiKey", ""),
            timeout=30,
        )
        model = data.get("model") or config["model"]
        return {"ok": True, "model": model}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "ignore")[:200]
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "error": "HTTP %d %s" % (exc.code, detail)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def build_corpus(reader, self_wxid: str, days: int, start=None, end=None, per_session_limit=MAX_MESSAGES_PER_SESSION):
    """把最近有活动的会话压成带编号的紧凑文本，同时保留编号到原文的映射。"""
    import time as _time
    from datetime import datetime

    since = start if start is not None else _time.time() - days * 86400
    sessions = [s for s in reader.sessions(limit=10000)
                if (s["sortAt"] or 0) >= since and s["category"] != "official"][:MAX_SESSIONS]

    lines = []
    index: dict[str, dict] = {}
    counter = 0
    for sess in sessions:
        msgs = (reader.messages(sess["username"], limit=per_session_limit, start=start, end=end)
                if start is not None else reader.messages(sess["username"], limit=per_session_limit))
        recent = [m for m in msgs if (m["at"] or 0) >= since]
        if not recent:
            continue
        lines.append("### 会话：%s（%s，%d 条）" % (sess["display"], "群聊" if sess.get("category") == "group" else "私聊", len(recent)))
        for m in recent[-per_session_limit:]:
            text = m["text"] if isinstance(m["text"], str) else str(m["text"])
            if not text:
                continue
            counter += 1
            who = "我" if m["senderId"] == self_wxid else (m["sender"] or "系统")
            stamp = datetime.fromtimestamp(m["at"]).strftime("%m-%d %H:%M") if m["at"] else ""
            line = "[%d] %s | %s | %s | %s" % (counter, sess["display"], who, stamp, text[:300])
            if sum(len(x) + 1 for x in lines) + len(line) > MAX_TOTAL_CHARS:
                continue
            lines.append(line)
            index[str(counter)] = {
                "conversationId": sess["username"],
                "conversationName": sess["display"],
                "serverId": m["serverId"],
                "sender": who,
                "at": m["at"],
                "timeText": m["timeText"],
                "text": text[:500],
            }

    corpus = "\n".join(lines)
    if len(corpus) > MAX_TOTAL_CHARS:
        # 超预算时保留尾部（较新的消息）并重新编号不可行，直接截断并告知
        corpus = corpus[:MAX_TOTAL_CHARS]
    return corpus, index, len(sessions)


def _extract_json(text):
    """Accept a JSON array or a recognized object wrapper, including fenced output."""
    if isinstance(text, list):
        text = ''.join(part.get('text', '') for part in text if isinstance(part, dict) and isinstance(part.get('text'), str))
    if not isinstance(text, str) or not text.strip():
        return None
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(text):
        if text[pos] not in '[{':
            pos += 1
            continue
        try:
            value, used = decoder.raw_decode(text[pos:])
        except json.JSONDecodeError:
            pos += 1
            continue
        pos += used
        if isinstance(value, list) and all(isinstance(x, dict) for x in value):
            return value
        if isinstance(value, dict):
            for key in ('items', 'insights', 'results', 'result', 'points', 'data'):
                found = value.get(key)
                if isinstance(found, list) and all(isinstance(x, dict) for x in found):
                    return found
            # Do not interpret nested evidence arrays as the model's answer.
    return None


def _request_array(payload, config):
    """Retry malformed model output once; never turn a parse error into success."""
    url = chat_url(config['baseUrl'])
    for attempt in range(2):
        request = dict(payload)
        if attempt:
            request['messages'] = list(payload['messages']) + [{
                'role': 'system', 'content': '上次输出格式无效。只返回合法 JSON 数组；不要注释、解释、占位符或省略号。没有事项时返回 []。'
            }]
        try:
            data = _post_json(url, request, config.get('apiKey', ''))
        except urllib.error.HTTPError as exc:
            return None, '模型返回 HTTP %d' % exc.code
        except (TimeoutError, socket.timeout):
            return None, '模型请求超时（180 秒）'
        except Exception as exc:
            return None, '模型请求失败：' + str(exc)[:160]
        try:
            choice = data['choices'][0]
            if choice.get('finish_reason') == 'length':
                return None, '模型输出达到长度限制'
            parsed = _extract_json(choice['message'].get('content'))
        except (KeyError, IndexError, TypeError, AttributeError):
            parsed = None
        if parsed is not None:
            return parsed, None
    return None, '模型两次未返回有效 JSON，请重试'


VALID_CATEGORIES = {"related", "opportunity", "group", "worth", "task", "task_update"}


def analyze(reader, self_wxid: str, config: dict, days: int = 5, start=None, end=None, known_tasks=None, per_session_limit=MAX_MESSAGES_PER_SESSION, user_name="") -> dict:
    """调模型归纳；返回带校验后的事项与统计。"""
    corpus, index, session_count = build_corpus(reader, self_wxid, days, start, end, per_session_limit)
    if not index:
        return {"ok": True, "source": "agent", "insights": [], "stats": {"sessions": 0, "evidence": 0},
                "note": "最近没有可分析的消息"}

    known_tasks = known_tasks or []
    task_context = [{"taskId": t["id"], "title": t["title"], "source": t.get("sourceText", "")[:200]}
                    for t in known_tasks if not t.get("done") and not t.get("dismissed")][:30]
    if user_name:
        corpus = "当前用户的昵称：%s。判断群聊被点名对象时需结合上下文；不能仅凭昵称相似推断。\n" % user_name[:80] + corpus
    if task_context:
        corpus += "\n\n已有待办（仅用于判断后续是否完成）：\n" + json.dumps(task_context, ensure_ascii=False)
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": corpus},
        ],
        "temperature": 0.2,
        "stream": False,
    }
    # DeepSeek defaults to high-effort thinking; bounded output and non-thinking
    # avoid spending most of the request window on reasoning for extraction.
    if config["model"].lower() in {"deepseek-v4.1-flash", "deepseek-flash"}:
        payload["reasoning_effort"] = "none"
        payload["max_tokens"] = 3000
    items, error = _request_array(payload, config)
    if error:
        return {"ok": False, "error": error, "insights": []}

    insights = []
    dropped_no_evidence = 0
    dropped_bad_evidence = 0
    for idx, raw in enumerate(items[:12]):
        if not isinstance(raw, dict):
            continue
        evidence = raw.get("evidence") or []
        if not isinstance(evidence, list):
            evidence = [evidence]
        refs = [index.get(str(e).strip("[]# ")) for e in evidence]
        refs = [r for r in refs if r]
        if not refs:
            # 引用不到原文的条目直接丢，宁缺毋滥
            if evidence:
                dropped_bad_evidence += 1
            else:
                dropped_no_evidence += 1
            continue

        category = raw.get("category")
        if category not in VALID_CATEGORIES:
            category = "worth"
        confidence = "inferred" if raw.get("confidence") == "inferred" else "direct"
        primary = refs[0]
        if category == "task_update" and str(raw.get("taskId") or "") not in {t["taskId"] for t in task_context}:
            continue
        assignee = raw.get("assignee") if raw.get("assignee") in ("self", "other", "unknown") else "unknown"
        due_text = str(raw.get("dueText") or "").strip()[:80]
        if due_text and not any(due_text in str(ref.get("text") or "") for ref in refs):
            due_text = ""
        insights.append({
            "assignee": assignee,
            "dueText": due_text,
            "taskId": str(raw.get("taskId") or ""),
            "id": "ai-%d" % idx,
            "category": category,
            "title": str(raw.get("title") or "").strip()[:80] or "未命名事项",
            "summary": str(raw.get("summary") or "").strip()[:300],
            "relation": str(raw.get("relation") or "").strip()[:200],
            "reason": "由模型归纳，引用了 %d 条原文" % len(refs),
            "at": primary["at"],
            "day": primary["timeText"][:10],
            "priority": 90 - idx * 4,
            "confidence": confidence,
            "status": "new",
            "sources": refs,
        })

    insights.sort(key=lambda x: (-x["priority"], -(x["at"] or 0)))
    return {
        "ok": True,
        "insights": insights,
        "source": "agent",
        "model": config["model"],
        "stats": {
            "sessions": session_count,
            "conversations": session_count,
            "messages": len(index),
            "insights": len(insights),
            "today": len([i for i in insights if i["day"] == __import__("datetime").datetime.now().strftime("%Y-%m-%d")]),
            "recentDays": days,
            "chars": len(corpus),
            "returned": len(items),
            "kept": len(insights),
            "droppedNoEvidence": dropped_no_evidence,
            "droppedBadEvidence": dropped_bad_evidence,
        },
    }

def summarize_daily(items: list[dict], config: dict, date: str, max_items: int = 60) -> dict:
    """Produce only source-linked summary points from already validated items."""
    candidates = []
    index = {}
    size = 0
    for item in items:
        if not item.get('sources'): continue
        number = str(len(index) + 1)
        line = '[%s] %s：%s' % (number, str(item.get('title') or '')[:80], str(item.get('summary') or '')[:180])
        if size + len(line) > 9000 or len(index) >= max_items: break
        size += len(line) + 1
        candidates.append(line)
        index[number] = item
    if not index:
        return {'ok':True,'points':[], 'used':0, 'total':len(items)}
    prompt = ('你是个人工作台的每日总结助手。只能依据编号事项归纳，不可添加未提及的人、日期或结论。'
              '输出 JSON 数组，最多 8 项；每项包含 kind（focus、task 或 opportunity）、text 和 evidence（输入编号数组）。'
              '每项至少引用一个有效编号；没有足够依据就省略。')
    payload = {'model':config['model'],'messages':[{'role':'system','content':prompt},
               {'role':'user','content':'日期：'+date+'\n'+'\n'.join(candidates)}],
               'temperature':0.2,'stream':False}
    if config['model'].lower() in {'deepseek-v4.1-flash','deepseek-flash'}:
        payload.update(reasoning_effort='none', max_tokens=2000)
    try:
        raw, error = _request_array(payload, config)
        if error: return {'ok':False,'error':'每日总结：'+error}
        points=[]
        for point in raw[:8]:
            if not isinstance(point,dict): continue
            evidence=point.get('evidence') or []
            if not isinstance(evidence,list): evidence=[evidence]
            if len(evidence) > 3: continue
            refs=[]
            for value in evidence:
                original=index.get(str(value).strip('[]# '))
                if original:
                    for ref in original.get('sources',[]):
                        if ref not in refs: refs.append(ref)
            text=str(point.get('text') or '').strip()[:300]
            if not refs or not text: continue
            kind=point.get('kind') if point.get('kind') in ('focus','task','opportunity') else 'focus'
            points.append({'kind':kind,'text':text,'sources':refs})
        if not points: return {'ok':False,'error':'每日总结未引用有效来源，请重试'}
        return {'ok':True,'points':points,'used':len(index),'total':len(items)}
    except (TimeoutError, socket.timeout):
        return {'ok':False,'error':'每日总结请求超时，请重试'}
    except Exception as exc:
        return {'ok':False,'error':'每日总结请求失败：'+str(exc)[:120]}
