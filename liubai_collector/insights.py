# -*- coding: utf-8 -*-
"""把真实会话与消息归纳成「事项」。

这里不调模型：先用可解释的规则产出候选，每条都带原文出处。
规则命中什么、为什么归到这一类，都能在界面上说清楚 —— 不猜、不编。
将来接模型时，这一层仍然是候选过滤与证据绑定。
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

# 合作/商机信号
OPPORTUNITY_WORDS = (
    "报价", "预算", "合同", "合作", "内训", "外包", "接单", "需求", "招标",
    "项目", "顾问", "多少钱", "费用", "开票", "发票", "签约", "对接",
)
# 承诺/待办信号
COMMIT_WORDS = ("我发你", "我整理好", "我改好", "明天给", "稍后发", "晚点发", "今晚发", "马上发", "我给你发", "我回你")
# 时间表述（模糊时间不换算成钟点）
TIME_HINT = re.compile(
    r"(今天|今晚|明天|后天|周一|周二|周三|周四|周五|周六|周日|下周|本周|月底|月初|上午|中午|下午|晚上)"
)
QUESTION_HINT = re.compile(r"[?？]|吗$|呢$|怎么|如何|能不能|是否|有没有|什么时候|多少")
WEEKDAY_CN = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}


def _clean(text: str, limit: int = 120) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t[:limit]


def _day_of(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""


def _category_for(text: str) -> tuple[str, str]:
    """返回 (category, 理由)。"""
    if any(w in text for w in OPPORTUNITY_WORDS):
        return "opportunity", "消息里出现了报价、需求、合同这类词"
    if any(w in text for w in COMMIT_WORDS):
        return "related", "消息里出现了承诺或待办"
    return "worth", "与你的会话有关，但没有明确的行动要求"


def _is_question(text: str) -> bool:
    return bool(QUESTION_HINT.search(text or ""))


def build_digest(reader, self_wxid: str, my_name: str,
                 recent_days: int = 3, per_session: int = 25,
                 max_insights: int = 24, start=None, end=None) -> dict:
    """扫描最近有活动的会话，产出带出处的事项列表。"""
    now = time.time()
    since = start if start is not None else now - recent_days * 86400
    sessions = reader.sessions(limit=10000)

    active = [s for s in sessions
              if (s["sortAt"] or 0) >= since or s["unread"] > 0]
    active = active[:120]

    insights: list[dict] = []
    scanned_msgs = 0

    for sess in active:
        username = sess["username"]
        if sess["category"] == "official":
            continue
        msgs = (reader.messages(username, limit=per_session, start=start, end=end)
                if start is not None else reader.messages(username, limit=per_session))
        scanned_msgs += len(msgs)
        recent = [m for m in msgs if (m["at"] or 0) >= since]
        if start is None and not recent:
            recent = msgs[-3:]
        if not recent:
            continue

        display = sess["display"]
        is_group = sess["category"] == "group"
        hit_mention = None
        hit_question = None
        hit_opportunity = None
        hit_commit = None

        for m in reversed(recent):
            text = m["text"] if isinstance(m["text"], str) else str(m["text"])
            if not text or text.startswith("["):
                continue
            mentioned = is_group and my_name and ("@" + my_name) in text
            mine = m["senderId"] == self_wxid if m["senderId"] else False

            if mentioned and hit_mention is None:
                hit_mention = m
            askable = (not is_group) or sess["unread"] > 0 or mentioned or len(recent) >= 3
            if askable and (not mine) and _is_question(text) and hit_question is None:
                hit_question = m
            if (not mine) and any(w in text for w in OPPORTUNITY_WORDS) and hit_opportunity is None:
                hit_opportunity = m
            if mine and any(w in text for w in COMMIT_WORDS) and hit_commit is None:
                hit_commit = m

        def source_of(m):
            return {
                "conversationId": username,
                "conversationName": display,
                "serverId": m["serverId"],
                "sender": m["sender"] or ("我" if m["senderId"] == self_wxid else "系统"),
                "at": m["at"],
                "timeText": m["timeText"],
                "text": _clean(m["text"], 200),
            }

        if hit_mention is not None:
            insights.append({
                "category": "related",
                "title": "「%s」有人点名找你" % display,
                "summary": "%s：%s" % (hit_mention["sender"] or "有人", _clean(hit_mention["text"])),
                "relation": "群里直接 @ 了你，通常在等你回话。",
                "reason": "消息里出现你的昵称",
                "at": hit_mention["at"],
                "priority": 95,
                "sources": [source_of(hit_mention)],
            })
        elif hit_question is not None:
            insights.append({
                "category": "related",
                "title": "「%s」有人在等回复" % display,
                "summary": "%s：%s" % (hit_question["sender"] or "对方", _clean(hit_question["text"])),
                "relation": "对方最后一条是提问，目前没有看到你的回复。",
                "reason": "最后一条对方消息是问句",
                "at": hit_question["at"],
                "priority": 78 if sess["unread"] else 66,
                "sources": [source_of(hit_question)],
            })

        if hit_opportunity is not None:
            insights.append({
                "category": "opportunity",
                "title": "「%s」出现合作相关表述" % display,
                "summary": "%s：%s" % (hit_opportunity["sender"] or "对方", _clean(hit_opportunity["text"])),
                "relation": "消息包含报价、需求或合同这类词，可能是线索。",
                "reason": "命中合作关键词",
                "at": hit_opportunity["at"],
                "priority": 84,
                "sources": [source_of(hit_opportunity)],
            })

        if hit_commit is not None:
            insights.append({
                "category": "related",
                "title": "你在「%s」里答应过一件事" % display,
                "summary": "你：%s" % _clean(hit_commit["text"]),
                "relation": "你在这条里说要交付具体东西，确认一下有没有发出去。",
                "reason": "你发出的消息含明确的交付措辞",
                "at": hit_commit["at"],
                "priority": 72,
                "sources": [source_of(hit_commit)],
            })

        if not (hit_mention or hit_question or hit_opportunity or hit_commit) and sess["unread"] >= 3:
            last = recent[-1]
            insights.append({
                "category": "worth",
                "title": "「%s」积了 %d 条未读" % (display, sess["unread"]),
                "summary": "%s：%s" % (last["sender"] or "对方", _clean(last["text"])),
                "relation": "这个会话近期比较活跃，但没看到直接指向你的事。",
                "reason": "未读数较高",
                "at": last["at"],
                "priority": 48,
                "sources": [source_of(last)],
            })

    insights.sort(key=lambda x: (-x["priority"], -(x["at"] or 0)))
    insights = insights[:max_insights]

    for idx, item in enumerate(insights):
        item["id"] = "live-%d" % idx
        item["day"] = _day_of(item["at"])
        item["confidence"] = "direct"
        item["status"] = "new"

    today = datetime.now().strftime("%Y-%m-%d")
    today_items = [i for i in insights if i["day"] == today]
    return {
        "day": today,
        "generatedAt": int(now),
        "insights": insights,
        "stats": {
            "conversations": len(active),
            "messages": scanned_msgs,
            "insights": len(insights),
            "today": len(today_items),
            "recentDays": recent_days,
        },
    }
