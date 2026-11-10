"""Account-local encrypted task store. Never writes into WeChat databases."""
import json
import hashlib
from difflib import SequenceMatcher
import os
import atomic_replace
import tempfile
import time
import uuid


def load(directory, unprotect):
    path = os.path.join(directory, 'tasks.bin')
    if not os.path.exists(path):
        return []
    with open(path, 'rb') as f:
        data = unprotect(f.read())
    if data is None:
        raise ValueError('待办解密失败，已保留原文件')
    obj = json.loads(data.decode('utf-8'))
    if not isinstance(obj, list):
        raise ValueError('待办数据格式错误，已保留原文件')
    return obj


def save(directory, tasks, protect):
    payload = protect(json.dumps(tasks, ensure_ascii=False).encode('utf-8'))
    if payload is None:
        raise ValueError('待办加密失败，未保存')
    os.makedirs(directory, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix='tasks-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        atomic_replace.replace(path, os.path.join(directory, 'tasks.bin'))
    finally:
        if os.path.exists(path):
            os.unlink(path)


def update(directory, body, protect, unprotect):
    tasks = load(directory, unprotect)
    op = body.get('op')
    if op == 'add':
        title = body.get('title', '').strip()
        if not title or len(title) > 200:
            raise ValueError('待办标题需为 1 至 200 字')
        tasks.insert(0, {'id': uuid.uuid4().hex, 'title': title,
                         'source': str(body.get('source') or '')[:300],
                         'done': False, 'createdAt': int(time.time())})
    elif op in ('toggle', 'delete'):
        task = next((t for t in tasks if t.get('id') == body.get('id')), None)
        if task is None:
            raise ValueError('待办不存在，请刷新')
        if op == 'delete':
            if task.get('origin') == 'model':
                task['dismissed'] = True
            else:
                tasks.remove(task)
        else:
            task['done'] = not task.get('done', False)
    else:
        raise ValueError('未知待办操作')
    save(directory, tasks, protect)
    return tasks


def visible(tasks):
    return [task for task in tasks if not task.get('dismissed')]


def merge_analysis(directory, insights, protect, unprotect):
    """Merge evidence-backed model tasks without resetting completed/dismissed state."""
    tasks = load(directory, unprotect)
    known = {task.get('evidenceKey') for task in tasks if task.get('evidenceKey')}
    added = 0
    changed = False
    for item in insights:
        if not item.get('sources'):
            continue
        ref = item['sources'][0]
        if item.get('category') == 'task_update':
            task = next((t for t in tasks if t.get('id') == item.get('taskId')), None)
            if (task and not task.get('done') and not task.get('dismissed')
                    and task.get('conversationId') == ref.get('conversationId')
                    and (ref.get('at') or 0) > (task.get('sourceAt') or 0)):
                task['completionSuggestion'] = {'text': str(ref.get('text') or '')[:500],
                                                'timeText': ref.get('timeText', ''),
                                                'serverId': ref.get('serverId', '')}
                changed = True
            continue
        if item.get('category') != 'task':
            continue
        conversation = ref.get('conversationId') or ''
        if not conversation:
            continue
        message_id = ref.get('serverId') or '%s:%s' % (ref.get('at', ''), ref.get('text', ''))
        key = hashlib.sha256((conversation + '\0' + str(message_id)).encode('utf-8')).hexdigest()
        title = str(item.get('title') or '').strip()[:200]
        if not title or title == '未命名事项':
            continue
        # One message can contain multiple actions. Keep clearly different titles,
        # while preserving done/dismissed state when a repeated batch rephrases one.
        if key in known:
            compact = lambda x: ''.join(str(x).lower().split())
            if any(t.get('evidenceKey') == key and
                   SequenceMatcher(None, compact(t.get('title', '')), compact(title)).ratio() >= 0.8
                   for t in tasks):
                continue
        tasks.insert(0, {'id': uuid.uuid4().hex, 'title': title,
                         'source': '%s · %s' % (ref.get('conversationName', ''), ref.get('timeText', '')),
                         'sourceText': str(ref.get('text') or '')[:500],
                         'origin': 'model', 'evidenceKey': key,
                         'conversationId': conversation, 'sourceAt': ref.get('at') or 0,
                         'assignee': item.get('assignee') if item.get('assignee') in ('self','other','unknown') else 'unknown',
                         'dueText': str(item.get('dueText') or '')[:80],
                         'done': False, 'createdAt': int(time.time())})
        known.add(key)
        added += 1
    if added or changed:
        save(directory, tasks, protect)
    return added
