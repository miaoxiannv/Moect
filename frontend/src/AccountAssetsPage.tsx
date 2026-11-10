import {ArchiveOptions,useArchivePreferences} from './ArchiveOptions'
import {useEffect,useMemo,useState} from 'react'
import {Download,RefreshCw,Sticker,Images,Check,BookOpen,KeyRound} from 'lucide-react'
import {Button} from '@/components/ui/button'
import {Dialog,DialogContent,DialogTitle} from '@/components/ui/dialog'
import {DateRangePicker} from '@/components/ui/date-range-picker'
import {Loader} from '@/components/ui/loader'
import {toast} from '@/components/ui/toast'
import {call} from './native'
import {AssetTutorial,useAssetTutorial} from './AssetTutorial'
import {WeChatText} from './WeChatText'

type Props={kind:'stickers'|'moments';library:any;busy:boolean;onConnect:()=>void;onExportQueued:()=>void;onRefreshQueued:()=>void}
const field='h-9 rounded-full bg-black/[0.04] dark:bg-white/[0.06] px-3 text-[13px] border-0 outline-none min-w-0'

export function AccountAssetsPage({kind,library,busy,onConnect,onExportQueued,onRefreshQueued}:Props){
  const tutorial=useAssetTutorial(kind)
  const packaging=useArchivePreferences(true)
  const [moreSettings,setMoreSettings]=useState(false)
  const [settingsSaving,setSettingsSaving]=useState(false)
  const [data,setData]=useState<any>(null),[loading,setLoading]=useState(false),[submitting,setSubmitting]=useState(false),[error,setError]=useState('')
  const [selected,setSelected]=useState<Set<string>>(new Set()),[download,setDownload]=useState(true)
  const [images,setImages]=useState(true),[videos,setVideos]=useState(true),[start,setStart]=useState(''),[end,setEnd]=useState('')
  const [momentScope,setMomentScope]=useState<'self'|'timeline'|'account'>('self'),[authorInput,setAuthorInput]=useState(''),[authors,setAuthors]=useState<any[]>([])
  const sticker=kind==='stickers',Icon=sticker?Sticker:Images
  useEffect(()=>{
    setMomentScope('self');setAuthorInput('');setAuthors([]);setStart('');setEnd('')
  },[library?.profile?.wxid,library?.demo])
  useEffect(()=>{
    let stopped=false;setData(null);setError('');setLoading(true);setSelected(new Set())
    if(!sticker&&start&&end&&start>end){setError('结束日期不能早于开始日期');setLoading(false);return}
    const timer=setTimeout(()=>{
      call('account_assets',{kind,scope:momentScope,authorId:authorInput,start,end}).then(result=>{if(stopped)return;setData(result);if(!sticker)setAuthors(result.authors||[]);setSelected(new Set((result.groups||[]).filter((g:any)=>g.count>0).map((g:any)=>g.id)))}).catch(e=>{if(!stopped)setError(e.message)}).finally(()=>{if(!stopped)setLoading(false)})
    },sticker?0:250)
    return()=>{stopped=true;clearTimeout(timer)}
  },[kind,library?.data_revision,library?.demo,library?.name,momentScope,authorInput,start,end,sticker])
  const count=useMemo(()=>(data?.groups||[]).filter((g:any)=>selected.has(g.id)).reduce((n:number,g:any)=>n+g.count,0),[data,selected])
  const refresh=async()=>{setSubmitting(true);try{await call('refresh_assets');toast.info('正在刷新表情包和朋友圈快照');onRefreshQueued()}catch(e){toast.error('刷新未完成',{description:(e as Error).message})}finally{setSubmitting(false)}}
  const recoverImages=async()=>{setSubmitting(true);try{await call('recover_images');toast.info('正在恢复图片读取，可在任务页取消');onRefreshQueued()}catch(e){toast.error('恢复未完成',{description:(e as Error).message})}finally{setSubmitting(false)}}
  const cacheStickers=async()=>{setSubmitting(true);try{await call('refresh_assets',{kind:'stickers',groups:[...selected]});toast.info('正在缓存所选表情资源');onRefreshQueued()}catch(e){toast.error('缓存未完成',{description:(e as Error).message})}finally{setSubmitting(false)}}
  const exportAll=async()=>{
    if(!sticker&&start&&end&&start>end){toast.warning('结束日期不能早于开始日期');return}
    setSubmitting(true)
    try{await packaging.flush();await call('export_account',{archiveZip:packaging.zip,archiveEncrypt:packaging.encrypt,archivePassword:packaging.password||undefined,kind,groups:[...selected],download,includeImages:images,includeVideos:videos,start,end,scope:momentScope,authorId:authorInput});onExportQueued()}
    catch(e){toast.error('无法创建导出任务',{description:(e as Error).message})}finally{setSubmitting(false)}
  }
  const closeMoreSettings=async()=>{
    setSettingsSaving(true)
    try{
      await packaging.savePassword()
      packaging.setPassword('')
    }catch(e){
      toast.error('设置保存失败',{description:(e as Error).message})
    }finally{
      setMoreSettings(false)
      setSettingsSaving(false)
    }
  }
  const toggle=(id:string)=>setSelected(previous=>{const result=new Set(previous);result.has(id)?result.delete(id):result.add(id);return result})
  return <><AssetTutorial kind={kind} open={tutorial.open} onClose={tutorial.close}/><div className="p-6 space-y-6 max-w-5xl">
    <div className="flex items-start justify-between gap-4"><div className="space-y-1.5"><h2 className="text-lg font-semibold flex items-center gap-2"><Icon className="w-5 h-5 text-primary"/>{sticker?'表情包':'朋友圈'}</h2></div>
      <div className="flex flex-wrap items-center justify-end gap-2">{!sticker&&<Button size="sm" variant="outline" className="rounded-full" onClick={recoverImages} disabled={!library?.live||busy||submitting}><KeyRound className="w-3.5 h-3.5 mr-1.5"/>恢复图片读取</Button>}<Button size="sm" variant="ghost" className="rounded-full" onClick={tutorial.show}><BookOpen className="w-3.5 h-3.5 mr-1.5"/>新手教程</Button><Button id={kind+'-refresh-resources'} size="sm" variant="outline" className="rounded-full" onClick={refresh} disabled={!library?.live||busy||submitting}><RefreshCw className="w-3.5 h-3.5 mr-1.5"/>{busy?'正在整理':'刷新资源快照'}</Button></div>
    </div>
    {!library||library.demo?<div className="rounded-xl bg-primary/5 p-5 space-y-3"><p className="text-sm">请先连接真实微信账号。</p><Button className="rounded-full" onClick={onConnect}>连接微信</Button></div>:<>
      {!sticker&&<div className="rounded-xl border border-border/70 p-5 space-y-4">
        <div className="flex flex-wrap items-center gap-2" aria-label="朋友圈导出范围">{([['self','我的朋友圈'],['timeline','公共朋友圈'],['account','指定账号']] as const).map(([id,label])=><Button key={id} size="sm" className="rounded-full" variant={momentScope===id?'default':'outline'} aria-pressed={momentScope===id} onClick={()=>setMomentScope(id)}>{label}</Button>)}</div>
        {momentScope==='account'&&<div className="space-y-2"><label className="block text-[13px] text-muted-foreground" htmlFor="moment-author">选择或填写微信账号</label><input id="moment-author" list="moment-author-options" className={field+' w-full'} placeholder="UID、微信号或可唯一匹配的昵称" value={authorInput} onChange={e=>setAuthorInput(e.target.value)}/><datalist id="moment-author-options">{authors.map(author=><option key={author.uid} value={author.uid} label={author.name+' · '+author.uid}/>)}</datalist><p className="text-xs text-muted-foreground">列表包含本机朋友圈缓存中出现的账号。重名时请选择具体 UID。</p></div>}
        {momentScope==='timeline'&&<p className="text-xs leading-relaxed text-muted-foreground">导出当前账号平时可读取、已缓存在本机的朋友圈动态；包含浏览账号主页时产生的缓存，不等于云端全部历史。</p>}
        <div className="max-w-xl space-y-2"><p className="text-[13px] text-muted-foreground">时间范围（北京时间）</p><DateRangePicker startTime={start} endTime={end} onChange={(s,e)=>{setStart(s);setEnd(e)}} emptyLabel="选择时间范围"/></div>
      </div>}
      {loading?<div className="flex items-center gap-3 py-12 text-sm text-muted-foreground"><Loader size={18}/>正在读取本机资源列表…</div>:error?<div className="rounded-xl border border-destructive/20 p-5 text-sm text-muted-foreground">{error}</div>:<>
        {!data?.available?<div className="rounded-xl border border-border/70 p-5 space-y-2"><p className="text-sm font-medium">尚无可读取的{sticker?'表情包':'朋友圈'}缓存</p><p className="text-[13px] text-muted-foreground">{sticker?'请在微信打开表情面板及相应专辑，然后刷新资源快照。':'请在微信浏览相应的朋友圈时间线或账号主页，再刷新资源快照。'}</p></div>:sticker?<>
          <div className="flex items-center justify-between"><p className="text-sm font-medium">{data.groups.length} 个分类 · 共 {data.total} 张 · 已选择 {count} 张</p><Button size="sm" variant="ghost" className="rounded-full" onClick={()=>setSelected(selected.size?new Set():new Set(data.groups.filter((g:any)=>g.count>0).map((g:any)=>g.id)))}>{selected.size?'取消全选':'全选'}</Button></div>
          <div className="grid grid-cols-2 xl:grid-cols-3 gap-3">{data.groups.map((group:any)=><button key={group.id} type="button" onClick={()=>toggle(group.id)} disabled={!group.count} aria-pressed={selected.has(group.id)} className={`p-4 text-left rounded-xl border transition-colors disabled:opacity-45 ${selected.has(group.id)?'bg-primary/5 border-primary/35':'border-border/70 hover:bg-accent/50'}`}>
            <div className="flex items-center gap-2.5"><div className={`w-7 h-7 rounded-full grid place-items-center shrink-0 ${selected.has(group.id)?'bg-primary text-primary-foreground':'bg-muted text-muted-foreground'}`}>{selected.has(group.id)?<Check className="w-4 h-4"/>:<Sticker className="w-4 h-4"/>}</div><div className="min-w-0"><p className="text-[13px] font-medium truncate" title={group.name}>{group.name}</p><p className="text-xs text-muted-foreground mt-1">{group.count?group.count+' 张表情':'尚未缓存专辑成员'}</p></div></div>
          </button>)}</div>
          <label className="flex gap-2 items-center text-[13px] text-muted-foreground"><input type="checkbox" checked={download} onChange={e=>setDownload(e.target.checked)}/>尝试下载本机尚未缓存的表情资源</label>
          <div className="flex flex-wrap gap-2"><Button variant="outline" className="rounded-full" disabled={!library?.live||!count||busy||submitting} onClick={cacheStickers}><RefreshCw className="w-4 h-4 mr-2"/>缓存所选表情资源</Button><Button className="rounded-full" disabled={!count||submitting||busy} onClick={exportAll}><Download className="w-4 h-4 mr-2"/>导出所选分类</Button><Button variant="outline" className="rounded-full" disabled={!packaging.loaded||submitting||busy} onClick={()=>setMoreSettings(true)}>更多设置</Button></div>
        </>:<>
          <div className="flex flex-wrap items-center gap-2"><p className="text-sm font-medium">{data.selection?.label||'朋友圈'} · {start||end?'所选时间内':'本地已缓存'} {data.count} 条记录</p>{data.unparsed>0&&<span className="text-xs text-muted-foreground">其中 {data.unparsed} 条结构暂未解析</span>}</div>
          <div className="rounded-xl border border-border/70 p-5 space-y-4">
            <div className="flex flex-wrap gap-5 text-[13px] text-muted-foreground"><label className="flex items-center gap-2"><input type="checkbox" checked={images} onChange={e=>setImages(e.target.checked)}/>导出图片</label><label className="flex items-center gap-2"><input type="checkbox" checked={videos} onChange={e=>setVideos(e.target.checked)}/>导出本地视频（可取消以节省空间）</label></div>
            <div className="flex flex-wrap gap-2"><Button className="rounded-full" disabled={busy||!data.count||submitting||loading||momentScope==='account'&&!authorInput.trim()} onClick={exportAll}><Download className="w-4 h-4 mr-2"/>导出所选朋友圈</Button><Button variant="outline" className="rounded-full" disabled={!packaging.loaded||submitting||busy} onClick={()=>setMoreSettings(true)}>更多设置</Button></div>
          </div>
          {data.posts?.length>0&&<div className="space-y-3">{data.posts.map((post:any)=><div key={post.id} className="rounded-xl bg-muted/35 p-4 space-y-2"><p className="text-xs text-muted-foreground"><span className="font-medium mr-2" title={post.author}>{post.author_name||post.author}</span>{post.create_time?new Date(post.create_time*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'时间待解析'}</p>{post.thumbnails?.length>0&&<div className="grid grid-cols-3 gap-1 max-w-[330px]">{post.thumbnails.slice(0,9).map((src:string,index:number)=><img key={index} src={src} className="aspect-square w-full object-cover rounded-sm" alt="朋友圈图片" loading="lazy"/>)}</div>}{post.image_count>(post.thumbnails?.length||0)&&<p className="text-xs text-muted-foreground">{post.media_status?.includes('encrypted')?'本地图片已缓存但尚未解密，请点击上方“恢复图片读取”。':`另有 ${post.image_count-(post.thumbnails?.length||0)} 张图片未预览，可先恢复图片读取；本地未缓存的内容需在微信打开后刷新。`}</p>}<p className="text-[13px] whitespace-pre-wrap break-words [overflow-wrap:anywhere] line-clamp-4"><WeChatText text={post.text||post.title||(post.status==='unsupported'?'本条缓存结构暂未解析':'图片或视频动态')}/></p></div>)}</div>}
        </>}
      </>}
    </>}
  </div>
  <Dialog open={moreSettings} onOpenChange={open=>{if(!open&&!settingsSaving)void closeMoreSettings()}}>
    <DialogContent className="p-6 space-y-5 max-h-[calc(100vh-48px)] overflow-y-auto">
      <DialogTitle>更多设置</DialogTitle>
      <ArchiveOptions model={packaging} disabled={submitting||busy||settingsSaving}/>
      <div className="flex justify-end">
        <Button className="rounded-full" disabled={settingsSaving} onClick={()=>void closeMoreSettings()}>完成</Button>
      </div>
    </DialogContent>
  </Dialog></>
}
