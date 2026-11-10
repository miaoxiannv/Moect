"""Bounded bridge from Moect to the local Liubai collector."""
import json
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:8791'


def request(path, body=None, timeout=10):
    raw = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(BASE + path, data=raw, headers={'Content-Type':'application/json'}, method='GET' if raw is None else 'POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            message = json.load(exc).get('error', '采集服务请求失败')
        except Exception:
            message = '采集服务请求失败'
        raise ValueError(message) from None
    except (urllib.error.URLError, OSError):
        raise ValueError('无法连接本地留白采集服务，请先启动采集服务。') from None
    return result


def checked(service):
    library = service.state().get('library') or {}
    wxid = (library.get('profile') or {}).get('wxid')
    status = request('/api/status')
    account = (status.get('state') or {}).get('account') or {}
    if not library.get('live') or library.get('demo') or not wxid:
        raise ValueError('请先在 Moect 连接真实微信账号。')
    if (status.get('state') or {}).get('phase') != 'ready' or account.get('wxid') != wxid:
        raise ValueError('请在留白采集服务连接与 Moect 相同的微信账号。')
    return account


def invoke(service, payload):
    operation = payload.get('operation')
    if operation == 'status':
        status = request('/api/status')
        return {'phase':(status.get('state') or {}).get('phase'),
                'accounts':[{'dirName':a['dirName'], 'wxid':a['wxid']} for a in status.get('accounts', [])]}
    if operation == 'connect':
        library = service.state().get('library') or {}
        wxid = (library.get('profile') or {}).get('wxid')
        status = request('/api/status')
        target = next((a for a in status.get('accounts',[]) if a.get('wxid') == wxid), None)
        if not library.get('live') or library.get('demo') or not wxid or not target:
            raise ValueError('请先在 Moect 连接真实微信，并确认相同账号在本机可用。')
        return request('/api/connect', {'dirName':target['dirName']})
    account = checked(service)
    if operation == 'scope':
        return request('/api/scope')
    if operation == 'save_scope':
        ids = payload.get('selected')
        if not isinstance(ids,list) or any(not isinstance(x,str) for x in ids):
            raise ValueError('会话选择无效')
        return request('/api/scope', {'account':account['dirName'],'selected':ids})
    if operation == 'config':
        data = request('/api/agent/config')
        return {k:data.get(k) for k in ('configured','baseUrl','model','hasKey')}
    if operation == 'automation':
        return request('/api/automation')
    if operation == 'set_automation':
        if not isinstance(payload.get('enabled'), bool):
            raise ValueError('自动分析设置无效')
        return request('/api/automation', {'account':account['dirName'], 'enabled':payload['enabled']}, timeout=40)
    if operation == 'daily':
        from urllib.parse import urlencode
        date = payload.get('date')
        if not isinstance(date, str): raise ValueError('日期无效')
        return request('/api/daily?' + urlencode({'date': date}))
    if operation == 'job':
        return request('/api/analysis/job')
    if operation == 'pause_job':
        return request('/api/analysis/pause', {'account':account['dirName']})
    if operation == 'start_job':
        scope = request('/api/scope')
        config = request('/api/agent/config')
        if not config.get('configured'):
            raise ValueError('请先在留白原设置中配置模型。')
        return request('/api/analysis/job', {'account':account['dirName'],
            'selected':[x['username'] for x in scope.get('sessions',[]) if x.get('selected')],
            'config':{'baseUrl':config['baseUrl'],'model':config['model']},
            'startDate':payload.get('startDate'),'endDate':payload.get('endDate')})
    if operation == 'analyze':
        scope = request('/api/scope')
        config = request('/api/agent/config')
        if not config.get('configured'):
            raise ValueError('请先在留白原设置中配置模型。')
        return request('/api/analyze', {'account':account['dirName'],
            'selected':[x['username'] for x in scope.get('sessions',[]) if x.get('selected')],
            'config':{'baseUrl':config['baseUrl'],'model':config['model']},
            'startDate':payload.get('startDate'),'endDate':payload.get('endDate')}, timeout=230)
    if operation == 'tasks':
        return request('/api/tasks')
    if operation == 'task_update':
        if payload.get('op') not in ('toggle','delete') or not isinstance(payload.get('id'),str):
            raise ValueError('待办操作无效')
        return request('/api/tasks', {'account':account['dirName'],'op':payload['op'],'id':payload['id']})
    raise ValueError('不支持的留白操作')
