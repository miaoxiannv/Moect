"""Bounded, cancellable export, derived from QCE's batch/writer pipeline."""
import csv
import hashlib
import html
import io
import json
import re
from contextlib import closing, nullcontext
from datetime import datetime
from pathlib import Path
from posixpath import relpath
from urllib.parse import quote
from .streaming import BufferedTextWriter
from .resources import ResourceStore
from .version import VERSION,APP_NAME
from .export_options import export_preferences,is_system_message
from .themes import theme_archive,archive_theme
from .inline_faces import ArchiveFaces

FORMATS = {'html', 'json', 'jsonl', 'txt', 'csv', 'md', 'xlsx'}


def safe_name(value):
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', value).strip(' .')[:70] or 'chat'
    if text.split('.')[0].upper() in {'CON','PRN','AUX','NUL', *['COM'+str(i) for i in range(1,10)], *['LPT'+str(i) for i in range(1,10)]}:
        text = '_' + text
    return text


def export_folder_name(name,uid,now=None):
    """Readable identity first, then a human-readable timestamp; never a bare UUID."""
    stamp=(now or datetime.now()).strftime('%Y-%m-%d_%H-%M-%S')
    return safe_name(str(name))[:36]+'_'+safe_name(str(uid))[:42]+'_'+stamp


def unused_folder(parent,name):
    candidate=parent/name;number=2
    while candidate.exists():
        candidate=parent/(name+'_'+str(number));number+=1
    return candidate


def clean(msg):
    if isinstance(msg,list):return [clean(value) for value in msg]
    if isinstance(msg,dict):
        return {k:clean(v) for k,v in msg.items() if not k.startswith('_') and k not in ('packed_hex','content')}
    return msg


def portable_paths(msg,prefix):
    if msg.get('avatar'):msg['avatar']=prefix+msg['avatar']
    for resource in msg.get('resources',[]):resource['path']=prefix+resource['path']
    if msg.get('quote'):portable_paths(msg['quote'],prefix)
    for child in (msg.get('forward') or {}).get('messages',[]):portable_paths(child,prefix)


def readable_text(msg,markdown=False):
    text=msg.get('text','')
    quote=msg.get('quote')
    if quote:
        reference=readable_text(quote,markdown)
        text+='\n'+(('> ' if markdown else '↳ ')+(quote.get('sender') or '')+': '+reference.replace('\n','\n> ' if markdown else '\n  '))
    forward=msg.get('forward')
    if forward:
        text+='\n【'+forward.get('title','转发的聊天记录')+'】'
        for child in forward.get('messages',[]):
            text+='\n['+child.get('time_str','')+'] '+child.get('sender','')+'\n'+readable_text(child,markdown)
        if forward.get('notice'):text+='\n'+forward['notice']
    for resource in msg.get('resources',[]):
        path=resource['path']
        if markdown:
            label='图片' if resource['kind']=='image' else resource['name']
            text+='\n'+('!' if resource['kind']=='image' and resource['status']=='available' else '')+'['+label.replace(']','')+'](<'+path+'>)'
        else:text+='\n'+path
    if msg.get('resource_notice'):text+='\n'+msg['resource_notice']
    return text


def csv_cell(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def materialize_forward(msg,folder,template,exported_at,cancel):
    """Write independent offline forward pages, plus complete JSON/TXT copies.

    Pages live beside the main HTML so media paths stay portable and cannot
    escape via '..'. Only explicitly referenced media are in the export folder.
    """
    if msg.get('quote'):materialize_forward(msg['quote'],folder,template,exported_at,cancel)
    forward=msg.get('forward')
    if not forward:return
    cancel();messages=forward.get('messages') or []
    identity=hashlib.sha256(json.dumps(forward,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:24]
    stem='forward_'+identity
    title=forward.get('title') or '转发的聊天记录'
    preview=[{'sender':m.get('sender',''),'text':m.get('text','')[:100]} for m in messages[:3]]
    meta={'application':APP_NAME,'version':VERSION,'name':title,
        'exported_at':exported_at,'count':len(messages),'forward':True,'notice':forward.get('notice','')}
    if not (folder/(stem+'.html')).exists():
        # Save full nested content before replacing child trees with page cards.
        with BufferedTextWriter(folder/(stem+'.json')) as writer:
            writer.write(json.dumps({'metadata':meta,'messages':messages},ensure_ascii=False))
        with BufferedTextWriter(folder/(stem+'.txt')) as writer:
            writer.write(title+'\n\n')
            for child in messages:
                cancel();writer.write('['+child.get('time_str','')+'] '+child.get('sender','')+'\n'+readable_text(child)+'\n\n')
            if forward.get('notice'):writer.write(forward['notice'])
        for child in messages:materialize_forward(child,folder,template,exported_at,cancel)
        chunks=[];batch=[];batch_bytes=0
        def flush():
            if not batch:return
            filename=stem+'_c%06d.js'%(len(chunks)+1)
            with BufferedTextWriter(folder/filename) as writer:
                writer.write('window.__wxChunk='+json.dumps(batch,ensure_ascii=False)+';')
            stamps=[int(child.get('create_time') or 0) for child in batch]
            chunks.append({'file':filename,'count':len(batch),'start_time':min(stamps),'end_time':max(stamps)})
            batch.clear()
        for child in messages:
            cancel();length=len(json.dumps(child,ensure_ascii=False).encode())
            if batch and (batch_bytes+length>4*1024*1024 or len(batch)>=500):flush();batch_bytes=0
            batch.append(child);batch_bytes+=length
        flush()
        manifest={**meta,'chunks':chunks,'downloads':{'json':stem+'.json','txt':stem+'.txt'}}
        config=json.dumps(manifest,ensure_ascii=False).replace('<','\\u003c')
        (folder/(stem+'.html')).write_text(template.replace('__MANIFEST__',config),encoding='utf-8')
    msg['forward']={k:forward[k] for k in ('title','count','total','truncated','notice') if k in forward}
    msg['forward'].update(title=title,count=len(messages),preview=preview,
        html=stem+'.html',json=stem+'.json',txt=stem+'.txt')


def export_session(archive, session, options, folder, viewer_assets, progress, cancel, status=None):
    fmt = options.get('format', 'html')
    if fmt not in FORMATS:
        raise ValueError('不支持的导出格式')
    title = session['display']
    stem = safe_name(title) + '_' + hashlib.sha256(session['username'].encode()).hexdigest()[:8]
    count = 0
    first_time = last_time = None
    out = folder / stem if fmt=='html' else folder / (stem+'_assets')
    out.mkdir(exist_ok=True)
    preferences=export_preferences(options)
    with (closing(ResourceStore(archive.data, session['username'], out, cancel, status, preferences))
          if hasattr(archive, 'data') else nullcontext()) as resources:
        def records():
            nonlocal first_time,last_time
            for msg in archive.messages(session['username'],options,cancel=cancel):
                if not preferences['include_system_messages'] and is_system_message(msg):continue
                first_time=msg['create_time'] if first_time is None else first_time
                last_time=msg['create_time']
                yield resources.enrich(msg) if resources else msg
        meta = dict(application=APP_NAME, version=VERSION, session=session['username'],
                    theme=archive_theme(options.get('_theme')),
                    name=title, exported_at=datetime.now().isoformat(timespec='seconds'),
                    time_zone='Asia/Shanghai',
                    export_options=preferences,
                    demo=bool(options.get('demo')), filters={k: options[k] for k in ('query','kind','start','end') if k in options})
        # Like QCE's text exporter, source iteration is separate from rendering; each
        # record is released immediately after writing. HTML rotates bounded chunks.
        if fmt == 'xlsx':
            from .excel_export import write_excel
            path = folder/(stem + '.xlsx')
            def prepare(message):
                portable_paths(message, out.name + '/')
                attachments = '\n'.join(resource['path'] for resource in message.get('resources', []) if resource.get('path'))
                return readable_text(message), attachments
            count = write_excel(path, records(), title, prepare, progress, cancel)
        elif fmt == 'html':
            faces = ArchiveFaces(viewer_assets, cancel)
            template = theme_archive((viewer_assets / 'viewer.html').read_text(encoding='utf-8'),meta['theme']).replace('__DATE_RANGE_PICKER__',(viewer_assets/'date-range-picker.js').read_text(encoding='utf-8')).replace('__ARCHIVE_PRINT__',(viewer_assets/'archive-print.js').read_text(encoding='utf-8')).replace('__ARCHIVE_MEDIA__',(viewer_assets/'archive-media.js').read_text(encoding='utf-8')).replace('__INLINE_FACES__',(viewer_assets/'inline-faces.js').read_text(encoding='utf-8'))
            chunks, batch = [], []
            batch_bytes = 0
            def flush():
                if not batch: return
                filename = 'c%06d.js' % (len(chunks)+1)
                with BufferedTextWriter(out / filename) as writer:
                    writer.write('window.__wxChunk=' + json.dumps(batch, ensure_ascii=False) + ';')
                stamps=[int(child.get('create_time') or 0) for child in batch]
                chunks.append(dict(file=filename,count=len(batch),start_time=min(stamps),end_time=max(stamps)))
                batch.clear()
            for msg in records():
                record=clean(msg)
                faces.remember(record)
                materialize_forward(record,out,template,meta['exported_at'],cancel)
                record_bytes=len(json.dumps(record,ensure_ascii=False).encode('utf-8'))
                if batch and batch_bytes+record_bytes>4*1024*1024:
                    flush();batch_bytes=0
                batch.append(record);batch_bytes+=record_bytes;count+=1
                if len(batch) >= 500:flush();batch_bytes=0
                if count % 200 == 0: progress(count)
            flush()
            faces.write(out)
            manifest = dict(**meta, count=count, chunks=chunks,
                            resource_summary=resources.stats if resources else {})
            config = json.dumps(manifest, ensure_ascii=False).replace('<', '\\u003c')
            (out / 'index.html').write_text(template.replace('__MANIFEST__', config), encoding='utf-8')
            path = out / 'index.html'
        else:
            path = folder / (stem + '.' + fmt)
            with BufferedTextWriter(path, encoding='utf-8-sig' if fmt=='csv' else 'utf-8') as writer:
                if fmt=='json': writer.write('{"metadata":' + json.dumps(meta, ensure_ascii=False) + ',"messages":[')
                if fmt=='jsonl': writer.write(json.dumps({'metadata':meta}, ensure_ascii=False)+'\n')
                if fmt in ('txt','md'):
                    writer.write(('# ' if fmt=='md' else '') + title + '\n' + ('演示数据 · ' if meta['demo'] else '') + '由 '+APP_NAME+' 导出\n\n')
                if fmt=='csv': writer.write('时间,会话,发送者,类型,内容,消息ID\r\n')
                for msg in records():
                    # Non-HTML exports keep resources next to the document. JSON
                    # paths are relative to the document, not the assets directory.
                    prefix=out.name+'/'
                    portable_paths(msg,prefix)
                    text=readable_text(msg,fmt=='md')
                    if fmt=='json':
                        writer.write((',' if count else '') + json.dumps(clean(msg), ensure_ascii=False))
                    elif fmt=='jsonl': writer.write(json.dumps(clean(msg), ensure_ascii=False)+'\n')
                    elif fmt=='csv':
                        buffer=io.StringIO(newline=''); row=csv.writer(buffer)
                        row.writerow([csv_cell(v) for v in [msg['time_str'], title, msg['sender'], msg['type_name'], text, msg['server_id']]])
                        writer.write(buffer.getvalue())
                    else:
                        writer.write(f"[{msg['time_str']}] {msg['sender']}\n{text}\n\n")
                    count += 1
                    if count % 200 == 0: progress(count)
                if fmt=='json': writer.write('],"count":'+str(count)+'}')
        cancel()
        progress(count)
        return dict(name=title, count=count, path=str(path), format=fmt,
                    start_time=first_time,end_time=last_time,
                    resource_summary=resources.stats if resources else {})


def export_index(folder, records, demo=False,theme=None):
    # Open HTML archives in the viewer; batch navigation stays inside it.
    if records and all(r['format']=='html' for r in records):
        paths=[Path(r['path']).relative_to(folder).as_posix() for r in records]
        if len(records)>1:
            for record,current in zip(records,paths):
                choices=''.join('<option value="%s"%s>%s</option>' % (
                    html.escape(quote(relpath(target,Path(current).parent.as_posix()),safe='/'),quote=True),
                    ' selected' if target==current else '',html.escape(item['name']))
                    for item,target in zip(records,paths))
                nav=('<nav aria-label="归档会话" style="margin-bottom:14px;font-size:12px;color:var(--muted-foreground)">'
                     '<label>切换会话 <select aria-label="切换会话" '
                     'style="font:inherit;color:var(--foreground);background:var(--card);border:1px solid var(--border);'
                     'border-radius:8px;padding:7px 10px;max-width:100%" '
                     'onchange="location.href=this.value">'+choices+'</select></label></nav>')
                path=Path(record['path'])
                content=path.read_text(encoding='utf-8')
                anchor='<div id="header-content">' if '<div id="header-content">' in content else '<header>'
                path.write_text(content.replace(anchor,anchor+nav,1),encoding='utf-8')
        target=quote(paths[0],safe='/')
        # Replace before paint, with a no-JS refresh and blocked-navigation link.
        content=('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                 '<title>'+html.escape(records[0]['name'])+' · Moect</title>'
                 '<meta http-equiv="refresh" content="0;url='+html.escape(target,quote=True)+'">'
                 '<script>location.replace('+json.dumps(target).replace('<','\\u003c')+');</script>'
                 '<a href="'+html.escape(target,quote=True)+'">打开聊天记录</a></html>')
        content=content.replace('<title>','<style>body{background:#f7f5fb;color:#292137}a{color:#7056c4}</style><title>',1)
        (folder/'index.html').write_text(theme_archive(content,theme),encoding='utf-8')
        return
    links = ''.join('<li><a href="%s">%s</a><span>%s 条 · %s</span></li>' %
                    (html.escape(quote(Path(r['path']).relative_to(folder).as_posix(),safe='/'), quote=True),
                     html.escape(r['name']), r['count'], r['format'].upper()) for r in records)
    content = '<!doctype html><meta charset="utf-8"><title>聊天记录归档</title><style>body{font:15px system-ui;max-width:850px;margin:70px auto;background:#f7f5fb;color:#292137}h1{font-size:32px}li{display:flex;justify-content:space-between;padding:20px;background:white;margin:10px 0;border-radius:12px}a{color:#7056c4;text-decoration:none}span{color:#7e718f}</style>'
    content += '<h1>聊天记录归档' + (' · 演示数据' if demo else '') + '</h1><p>'+APP_NAME+' · 本地归档</p><ul>' + links + '</ul>'
    (folder/'index.html').write_text(theme_archive(content,theme), encoding='utf-8')
