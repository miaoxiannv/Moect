"""Portable sticker albums and explicitly scoped cached Moments exports."""
import hashlib
import json
import os
import base64
import time
from datetime import datetime,timezone,timedelta
from pathlib import Path
from itertools import chain

from .account_assets import StickerCatalog,MomentsCatalog,HASH,moment_selection
from .avatars import image_type
from .exporting import safe_name,clean
from .resources import ResourceStore
from .sticker_cache import StickerCache
from .media_codec import encrypted_media_variants,media_type
from .version import APP_NAME,VERSION
from .sns_codec import sns_variants
from .themes import theme_archive,archive_theme
from .inline_faces import ArchiveFaces


def write_collection(folder,assets,data,cancel=lambda:None):
    faces=ArchiveFaces(assets,cancel)
    if data.get('category')=='moments':
        for item in data.get('items',[]):
            faces.remember_text(item.get('text'))
            for comment in item.get('comments',[]):
                faces.remember_text(comment.get('text'))
    faces.write(folder)
    data['theme']=archive_theme(data.get('theme'))
    (folder/'data.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    encoded=json.dumps(data,ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    template=theme_archive((assets/'collection.html').read_text(encoding='utf-8'),data['theme']).replace('__DATE_RANGE_PICKER__',(assets/'date-range-picker.js').read_text(encoding='utf-8')).replace('__ARCHIVE_PRINT__',(assets/'archive-print.js').read_text(encoding='utf-8')).replace('__ARCHIVE_MEDIA__',(assets/'archive-media.js').read_text(encoding='utf-8')).replace('__INLINE_FACES__',(assets/'inline-faces.js').read_text(encoding='utf-8'))
    (folder/'index.html').write_text(template.replace('__COLLECTION__',encoded),encoding='utf-8')


def metadata(name,category,profile):
    return {'application':APP_NAME,'version':VERSION,'category':category,'name':name,'owner':{'name':profile['name'],'uid':profile['wxid']},
            'exported_at':datetime.now().isoformat(timespec='seconds'),'time_zone':'Asia/Shanghai','partial_local_cache':True}


def export_stickers(archive,library,payload,folder,assets,progress,cancel,status):
    catalog=StickerCatalog(library,cancel)
    wanted=payload.get('groups')
    if not isinstance(wanted,list) or not all(isinstance(v,str) for v in wanted):raise ValueError('请选择需要导出的表情分类。')
    by_id={g['id']:g for g in catalog.groups}
    if not wanted or any(key not in by_id for key in wanted):raise ValueError('表情分类已变化，请刷新后重新选择。')
    selected=[g for g in catalog.groups if g['id'] in set(wanted)]
    download=payload.get('download',True)
    if not isinstance(download,bool):raise ValueError('下载选项无效')
    total=sum(len(g['members']) for g in selected)
    if not total:raise ValueError('所选分类中没有可导出的表情。')
    profile=archive.profile();name=profile['name']+' · 表情包'
    data=metadata(name,'stickers',profile);data.update(groups=[],items=[],notice=catalog.summary()['notice'],theme=archive_theme(payload.get('_theme')))
    resources=ResourceStore(archive.data,'',folder,cancel,status,{'includeAvatars':False})
    decoder=StickerCache(archive.data,resources.codec,cancel,status);count=0
    try:
        for group_number,group in enumerate(selected,1):
            cancel();relative=Path('表情包')/(str(group_number).zfill(2)+'_'+safe_name(group['name'])[:40])
            destination=folder/relative;destination.mkdir(parents=True,exist_ok=True)
            data['groups'].append({'id':group['id'],'name':group['name'],'count':len(group['members'])})
            for number,member in enumerate(group['members'],1):
                cancel();count+=1;progress(count,total)
                image,quality=decoder.resolve(member,resources,download)
                item={'id':group['id']+'_'+member['md5'],'group':group['id'],'name':member.get('caption') or '表情 '+str(number),
                      'md5':member['md5'],'status':'available' if image else 'missing','quality':quality,'resources':[]}
                if image:
                    extension=image_type(image)[0]
                    filename=str(number).zfill(3)+'_'+safe_name(member.get('caption') or member['md5'][:12])[:50]+'.'+extension
                    path=relative/filename;(folder/path).write_bytes(image)
                    item['resources']=[{'kind':'image','path':path.as_posix(),'status':'available','role':'thumbnail' if quality=='thumbnail' else 'main'}]
                    resources.stats['available']+=1
                else:resources.stats['missing']+=1
                resources.report.write(json.dumps({k:item[k] for k in ('id','group','name','md5','status','quality')},ensure_ascii=False)+'\n')
                data['items'].append(item)
        data['resource_summary']=dict(resources.stats)
    finally:resources.close()
    cancel();write_collection(folder,assets,data,cancel)
    return {'name':name,'path':str(folder/'index.html'),'count':count,'format':'stickers','resource_summary':data['resource_summary']}


class MomentResources:
    def __init__(self,resources,cache=None,index_seconds=None,max_image_bytes=64*1024*1024):
        self.resources=resources;self.index=None;self.cache=Path(cache).resolve() if cache else None;self.cached={}
        self.index_seconds=index_seconds;self.max_image_bytes=max_image_bytes
        self.key_samples=None;self.last_failure=None
        if self.cache and (self.cache/'media.json').is_file():
            try:self.cached=json.loads((self.cache/'media.json').read_text(encoding='utf-8'))
            except (OSError,ValueError):pass

    @staticmethod
    def media_key(item):return hashlib.sha256(json.dumps(item,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

    def local_candidates(self,hints):
        if self.index is None:
            self.index={};account=Path(self.resources.data.account_dir()).resolve();cache=account/'cache'
            deadline=time.monotonic()+self.index_seconds if self.index_seconds is not None else float('inf')
            if cache.is_dir():
                for month in sorted(cache.iterdir(),reverse=True):
                    if time.monotonic()>=deadline:break
                    if not month.is_dir() or month.is_symlink():continue
                    base=month/'Sns'
                    if not base.is_dir() or base.is_symlink():continue
                    for directory,dirs,files in os.walk(base,followlinks=False):
                        if time.monotonic()>=deadline:break
                        self.resources.cancel();dirs[:]=[n for n in dirs if not (Path(directory)/n).is_symlink()]
                        for name in files:
                            if time.monotonic()>=deadline:break
                            p=Path(directory)/name
                            if p.is_symlink() or not p.resolve().is_relative_to(account):continue
                            stem=p.stem.lower()
                            digest=stem if HASH.fullmatch(stem) else (p.parent.name+stem).lower()
                            if HASH.fullmatch(digest):self.index.setdefault(digest,[]).append(p)
        result=[]
        for digest in hints.get('hashes',[]):
            for path in self.index.get(digest,[]):
                thumbnail=digest in hints.get('thumbnail_hashes',[]) and digest not in hints.get('main_hashes',[]) or path.suffix.lower()=='.thumb' or any('thumb' in part.lower() for part in path.parts)
                result.append((path,'thumbnail' if thumbnail else 'main'))
        result.sort(key=lambda row:(row[1]!='main',row[0].suffix.lower()=='.tmp'))
        return result

    def convert(self,path,kind,hints):
        store=self.resources
        self.last_failure={'status':'corrupt','reason':'image_decode_failed'}
        try:
            if path.stat().st_size>(256*1024*1024 if kind=='video' else self.max_image_bytes):
                self.last_failure={'status':'unsupported','reason':'file_too_large'};return None
            if kind=='video':
                with path.open('rb') as stream:signature=media_type(stream.read(32))
                original=path
                if not signature or not signature[1].startswith('video/'):
                    # SNS video encryption affects only the first 128 KiB.
                    with path.open('rb') as stream:prefix=stream.read(128*1024)
                    decoded=next((candidate for candidate in sns_variants(prefix,hints.get('stream_keys',[]),store.cancel)
                                  if media_type(candidate) and media_type(candidate)[1].startswith('video/')),None)
                    if decoded is None:return None
                    target=Path(store.tmp.name)/'sns-decrypted.mp4'
                    with path.open('rb') as source,target.open('wb') as output:
                        source.seek(len(prefix));output.write(decoded)
                        while True:
                            store.cancel();block=source.read(1024*1024)
                            if not block:break
                            output.write(block)
                    path=target
                result=store.codec.video(path,Path(store.tmp.name)/'sns-playback.mp4')
                if result:return store._copy(result,'video',extension='.mp4',identity_source=original)
            else:
                raw=path.read_bytes()
                if raw.startswith((b'\x07\x08V1',b'\x07\x08V2')):
                    # Local SNS caches use the same DAT envelope as chat images.
                    # CDN/ISAAC decoding alone cannot read these files.
                    if self.key_samples is None:
                        from .image_recovery import recent_image_samples
                        self.key_samples=recent_image_samples(store.data,store.cancel) if store.keys.scan_remaining>0 else []
                    decoded=store.keys.decode(raw,path,self.key_samples,hints.get('keys',[]))
                    image=store.keys.rendered or (store.codec.image(decoded) if decoded else None)
                    if image:return store._copy(path,'image',image,'.'+image_type(image)[0])
                    reason=store.keys.reason or 'image_decode_failed'
                    self.last_failure={'status':'encrypted' if reason in ('aes_key_unavailable','xor_unresolved') else 'corrupt',
                                       **store.keys.diagnostics,'reason':reason}
                    return None
                for blob in chain((raw,),sns_variants(raw,hints.get('stream_keys',[]),store.cancel),encrypted_media_variants(raw,hints.get('keys',[]),store.cancel)):
                    image=(store.codec.sticker if hints.get('animated') else store.codec.image)(blob)
                    if image:return store._copy(path,'image',image,'.'+image_type(image)[0])
        except OSError:self.last_failure={'status':'missing','reason':'local_file_unreadable'}
        except ValueError:pass
        return None

    def resolve(self,item,include_images,include_videos):
        store=self.resources;kind=item['kind'];hints=item['_media']
        if (kind=='image' and not include_images) or (kind=='video' and not include_videos):
            store.stats['skipped']+=1;return {'kind':kind,'status':'skipped','resources':[]}
        cached=self.cached.get(self.media_key(item))
        if cached and self.cache:
            copied=[]
            for resource in cached.get('resources',[]):
                source=(self.cache/resource['path']).resolve()
                if not source.is_relative_to(self.cache) or not source.is_file():continue
                if resource['kind']=='image' and source.stat().st_size>self.max_image_bytes:continue
                result=store._copy(source,resource['kind'],extension=source.suffix)
                if result:
                    if resource.get('role'):result['role']=resource['role']
                    copied.append(result)
            if copied:return {**cached,'resources':copied}
        thumbnails=[];failures=[]
        for source,role in self.local_candidates(hints):
            if role=='thumbnail':thumbnails.append(source);continue
            resource=self.convert(source,kind,hints)
            if resource:return {'kind':kind,'status':'available','resources':[resource]}
            if self.last_failure:failures.append(dict(self.last_failure))
        if include_images:
            for source in thumbnails:
                resource=self.convert(source,'image',hints)
                if resource:
                    resource['role']='thumbnail';return {'kind':kind,'status':'thumbnail','resources':[resource]}
                if self.last_failure:failures.append(dict(self.last_failure))
        if failures:
            failure=next((value for value in failures if value['status']=='encrypted'),failures[0])
            store.stats[failure['status']]+=1
            return {'kind':kind,'status':failure['status'],'reason':failure['reason'],
                    'diagnostics':failure,'resources':[]}
        store.stats['missing']+=1;return {'kind':kind,'status':'missing','resources':[]}


def local_moments_previews(archive,library,selection,start=None,end=None):
    """Read local media only. No WeChat UI, process scan or network request."""
    import io
    import tempfile
    import time
    from PIL import Image
    posts=[]
    for post in MomentsCatalog(library,archive.profile()['wxid'],selection['scope'],selection['author_id']).posts():
        if start is not None and post['create_time']<start or end is not None and post['create_time']>end:continue
        posts.append(post)
        if len(posts)>=12:break
    previews=[];cache=Path(library['root']).parent/'moments-thumbnails';deadline=time.monotonic()+6
    with tempfile.TemporaryDirectory(prefix='moect-moments-preview-') as temporary:
        resources=ResourceStore(archive.data,'',Path(temporary),options={'includeAvatars':False})
        resources.keys.scan_remaining=0 # Previews reuse saved credentials; no process scans.
        resolver=MomentResources(resources,Path(library.get('assets_root') or library['root'])/'preview',index_seconds=2,max_image_bytes=8*1024*1024)
        try:
            for post in posts:
                item={k:post.get(k) for k in ('id','author','text','title','create_time','status')}
                item['author_name']=archive.data.name_of(post['author']) or post['author'];item['thumbnails']=[];item['media_status']=[]
                item['image_count']=sum(m['kind']=='image' for m in post['media'][:9])
                for media in post['media'][:9]:
                    if media['kind']!='image':continue
                    marker=hashlib.sha256((str(library.get('assets_revision',''))+MomentResources.media_key(media)).encode()).hexdigest()
                    target=cache/(marker+'.jpg');blob=None
                    try:
                        if target.is_file() and target.stat().st_size<=128*1024:blob=target.read_bytes()
                        elif time.monotonic()<deadline:
                            result=resolver.resolve(media,True,False)
                            item['media_status'].append(result['status'])
                            for resource in result['resources']:
                                if resource['kind']!='image':continue
                                with Image.open(Path(temporary)/resource['path']) as image:
                                    image.seek(0);image.thumbnail((220,220));output=io.BytesIO();image.convert('RGB').save(output,format='JPEG',quality=72)
                                blob=output.getvalue();cache.mkdir(parents=True,exist_ok=True);target.write_bytes(blob);break
                    except (OSError,ValueError):pass
                    if blob:item['thumbnails'].append('data:image/jpeg;base64,'+base64.b64encode(blob).decode())
                previews.append(item)
        finally:resources.close()
    return previews


def export_moments(archive,library,payload,folder,assets,progress,cancel,status):
    images=payload.get('includeImages',True);videos=payload.get('includeVideos',True)
    if not isinstance(images,bool) or not isinstance(videos,bool):raise ValueError('资源导出选项无效')
    profile=archive.profile();selection=moment_selection(payload,profile['wxid'],archive.data.contacts)
    catalog=MomentsCatalog(library,profile['wxid'],selection['scope'],selection['author_id']);name=profile['name']+' · '+selection['label']
    data=metadata(name,'moments',profile);data.update(items=[],scope=selection,theme=archive_theme(payload.get('_theme')),filters={k:payload[k] for k in ('start','end') if payload.get(k)},
        notice='范围：'+selection['label']+'。包含本次已整理的可访问动态、评论和点赞；微信权限和资源有效期可能使结果不完整。HTML 内可按作者和时间继续筛选本次归档。')
    resources=ResourceStore(archive.data,'',folder,cancel,status,{'includeAvatars':False})
    resolver=MomentResources(resources,Path(library.get('assets_root') or library['root'])/'preview')
    start=payload.get('_start');end=payload.get('_end');count=0
    try:
        for post in catalog.posts(cancel):
            cancel()
            if (start is not None and post['create_time']<start) or (end is not None and post['create_time']>end):continue
            count+=1;progress(count,0);item=clean(post);item['resources']=[];item['media_status']=[];item['media_diagnostics']=[]
            item['author_name']=archive.data.name_of(post['author']) or post['author']
            item['author_avatar']=archive.avatars.export(post['author'],folder) or ''
            for person in item.get('likes',[])+item.get('comments',[]):
                cancel();uid=person['uid'];contact=archive.data.contacts.get(uid) or {}
                person['name']=contact.get('remark') or contact.get('nick_name') or person['name'] or uid
                person['avatar']=archive.avatars.export(uid,folder) if uid else ''
                if person.get('reply_uid'):
                    person['reply_name']=archive.data.name_of(person['reply_uid']) or person.get('reply_name') or person['reply_uid']
            for source,comment in zip(post.get('comments',[]),item.get('comments',[])):
                comment['resources']=[]
                for media in source.get('_attachments',[]):
                    cancel();result=resolver.resolve(media,images,False);comment['resources'].extend(result['resources'])
            if post['status']=='unsupported':resources.stats['unsupported']+=1
            for media in post['media']:
                cancel();result=resolver.resolve(media,images,videos);item['resources'].extend(result['resources']);item['media_status'].append(result['status'])
                item['media_diagnostics'].append({k:result[k] for k in ('kind','status','reason','diagnostics') if k in result})
            item.pop('media',None)
            if item['create_time']:
                try:item['time_str']=datetime.fromtimestamp(item['create_time'],timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
                except (ValueError,OverflowError,OSError):item['time_str']='时间未知'
            resources.report.write(json.dumps({'id':item['id'],'status':item['status'],'media_status':item['media_status'],
                                               'media_diagnostics':item['media_diagnostics']},ensure_ascii=False)+'\n')
            data['items'].append(item)
        data['resource_summary']=dict(resources.stats)
    finally:resources.close()
    if not catalog.available:raise ValueError('尚无可读取的朋友圈数据库。请先在微信浏览朋友圈，再刷新资源快照。')
    if not count:raise ValueError('本机尚未缓存所选范围和时间段的朋友圈。请在微信浏览相应时间线或账号主页，再刷新资源快照。')
    data['items'].sort(key=lambda p:(p['create_time'],p['id']),reverse=True)
    cancel();write_collection(folder,assets,data,cancel)
    timestamps=[p['create_time'] for p in data['items'] if p['create_time']]
    return {'name':name,'path':str(folder/'index.html'),'count':count,'format':'moments','resource_summary':data['resource_summary'],
            'scope':selection,'start_time':min(timestamps) if timestamps else None,'end_time':max(timestamps) if timestamps else None}
