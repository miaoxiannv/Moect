import {StoragePage} from './StoragePage'
import {PersonalBrief} from './PersonalBrief'
// Adapted from shuakami/qq-chat-exporter qce-v4-tool/app/page.tsx (GPL-3.0).
// Layout/class names are preserved; only local backend bindings and supported
// WeChat feature entries replace the QQ/NapCat-specific ones.
import {useState,useEffect,useCallback,useRef,useMemo} from 'react'
import type {MouseEvent} from 'react'
import {revealTheme} from './themeReveal'
import {ThemeToggleIcon} from './ThemeToggleIcon'
import './theme-toggle.css'
import {motion,AnimatePresence} from 'framer-motion'
import {Activity,MessageCircle,Zap,Settings,HelpCircle,FileText,PanelLeftClose,PanelLeftOpen,RefreshCw,ExternalLink,LogOut,Sticker,Images,Check} from 'lucide-react'
import {Button} from '@/components/ui/button'
import {WindowDragRegion,WindowControls} from '@/components/ui/window-controls'
import {Avatar,AvatarFallback,AvatarImage} from '@/components/ui/avatar'
import {Badge} from '@/components/ui/badge'
import {Loader} from '@/components/ui/loader'
import {SessionList} from '@/components/ui/session-list'
import {BatchExportDialog,type BatchExportItem,type BatchExportConfig} from '@/components/ui/batch-export-dialog'
import {MessagePreviewModal} from '@/components/ui/message-preview-modal'
import {Dialog,DialogContent,DialogTitle} from '@/components/ui/dialog'
import {TooltipProvider} from '@/components/ui/tooltip'
import {toast,Toaster} from '@/components/ui/toast'
import type {Group,Friend} from '@/types/api'
import {call,ready} from './native'
import {TaskRow} from './TaskRow'
import {TasksPage} from './TasksPage'
import {AccountAssetsPage} from './AccountAssetsPage'
import {useWindowEdges} from './useWindowEdges'
import {AppTutorial} from './AppTutorial'
import {useAssetTutorial} from './AssetTutorial'

const titles:Record<string,string>={brief:'每日简报',overview:'概览',sessions:'会话',tasks:'任务',stickers:'表情包',moments:'朋友圈',settings:'设置',about:'关于'}
const active=(status:string)=>status==='queued'||status==='running'
const PILL='h-[36px] px-3.5 rounded-full border-0 bg-black/[0.04] dark:bg-white/[0.06] text-[13px] outline-none placeholder:text-muted-foreground/70 focus:bg-black/[0.06] dark:focus:bg-white/[0.09] transition-colors'

export function App(){
  useWindowEdges()
  const [state,setState]=useState<any>(null),[tab,setTab]=useState('overview'),[sidebar,setSidebar]=useState(true)
  const onboarding=useAssetTutorial('app',!!state),demoReturnTab=useRef('overview')
  const [sessions,setSessions]=useState<any[]>([]),[loading,setLoading]=useState(false)
  const [batch,setBatch]=useState(false),[selected,setSelected]=useState(new Set<string>())
  const [exportItems,setExportItems]=useState<BatchExportItem[]>([]),[exportOpen,setExportOpen]=useState(false)
  const [preview,setPreview]=useState<any>(null),[previewOpen,setPreviewOpen]=useState(false)
  const [connectOpen,setConnectOpen]=useState(false),[detected,setDetected]=useState<any>(null),[account,setAccount]=useState(''),[connectError,setConnectError]=useState('')
  const [connecting,setConnecting]=useState(false)
  const [changingAppearance,setChangingAppearance]=useState(false),appearanceBusy=useRef(false)
  const connectJob=useRef(''),autoConnect=useRef(false),refreshing=useRef(false),stateRef=useRef<any>(null)
  const libraryEpoch=useRef(0),loadSequence=useRef(0),changingLibrary=useRef(false)
  const pendingSort=useRef<any>(null),sortSequence=useRef(0),sortQueue=useRef<Promise<any>>(Promise.resolve())
  const report=(error:any)=>toast.error('操作未完成',{description:error.message,duration:Infinity})
  const update=useCallback((data:any,appearanceOnly=false)=>{
    const current=stateRef.current
    // Only explicit appearance actions change an initialized theme. A delayed
    // poll must not undo it, nor may a settings reply restore older task data.
    if(current)data=appearanceOnly
      ? {...current,theme:data.theme,accent:data.accent,appearance:data.appearance}
      : {...data,theme:current.theme,accent:current.accent,appearance:current.appearance}
    if(pendingSort.current)data={...data,session_sort:pendingSort.current}
    if(JSON.stringify(current)!==JSON.stringify(data))setState(data)
    stateRef.current=data
    document.documentElement.classList.toggle('dark',data.theme==='dark')
    for(const [key,value] of Object.entries(data.appearance||{}))document.documentElement.style.setProperty('--'+key,String(value))
  },[])
  const saveSessionSort=useCallback((preference:any)=>{
    const sequence=++sortSequence.current;pendingSort.current=preference
    update({...stateRef.current,session_sort:preference})
    sortQueue.current=sortQueue.current.catch(()=>{}).then(()=>call('settings',{session_sort:preference})).then(data=>{
      if(sequence===sortSequence.current){pendingSort.current=null;update(data)}
    }).catch(error=>{if(sequence===sortSequence.current){pendingSort.current=null;report(error)}})
  },[update])
  const load=useCallback(async()=>{const epoch=libraryEpoch.current,sequence=++loadSequence.current;setLoading(true);try{const rows=await call('sessions');if(epoch===libraryEpoch.current&&sequence===loadSequence.current)setSessions(rows)}finally{if(epoch===libraryEpoch.current&&sequence===loadSequence.current)setLoading(false)}},[])
  const refresh=useCallback(async()=>{
    if(refreshing.current||changingLibrary.current)return
    refreshing.current=true;const epoch=libraryEpoch.current
    try{
      const old=stateRef.current,data=await call('state')
      if(epoch!==libraryEpoch.current)return
      update(data)
      if(connectJob.current){
        const t=data.tasks.find((t:any)=>t.id===connectJob.current)
        if(t?.status==='completed'){
          const automatic=autoConnect.current;connectJob.current='';autoConnect.current=false
          await load();if(epoch!==libraryEpoch.current)return
          if(!automatic)setTab('sessions')
          toast.success(automatic?'微信聊天记录已自动更新':'微信归档已就绪')
        }else if(t&&['failed','cancelled'].includes(t.status)){
          const automatic=autoConnect.current;connectJob.current='';autoConnect.current=false
          if(t.status==='failed')automatic?toast.warning('自动连接未完成',{description:t.detail}):report(new Error(t.detail))
        }
      }
      if(old)for(const t of data.tasks){const prev=old.tasks.find((p:any)=>p.id===t.id);if((!prev&&t.kind!=='recover_images')||prev?.status===t.status)continue;if(t.status==='completed'&&t.kind==='export')toast.success('导出完成',{description:t.detail,action:{label:'查看任务',onClick:()=>setTab('tasks')}});if(t.kind==='refresh_assets'&&t.status==='completed')toast.success('表情包和朋友圈快照已更新');if(t.kind==='refresh_assets'&&t.status==='failed')report(new Error(t.detail));if(t.kind==='recover_images'&&t.status==='completed'){toast.success('图片读取已恢复',{description:t.detail});window.dispatchEvent(new Event('moect-media-recovered'))}if(t.kind==='recover_images'&&t.status==='failed')report(new Error(t.detail))}
    }catch(error){if(epoch===libraryEpoch.current)report(error)}finally{refreshing.current=false}
  },[load,update])
  useEffect(()=>{
    let timer:any,stopped=false
    ready.then(async()=>{
      try{
        // Read before mounting the tutorial: displaying it persists "seen".
        // Finishing it must not start a deferred reconnect in this first run.
        const [data,tutorial]=await Promise.all([call('state'),call('tutorial_status',{kind:'app'}).catch(()=>({seen:false}))])
        if(stopped)return;update(data)
        if(data.library)try{await load()}catch(error){report(error)}
        if(stopped)return
        window.__ready=true;timer=setInterval(refresh,1200)
        if(tutorial.seen!==true)return
        const epoch=libraryEpoch.current,result=await call('startup_reconnect')
        if(stopped||epoch!==libraryEpoch.current)return
        if(result.job){connectJob.current=result.job;autoConnect.current=true;toast.info('正在自动重新连接并整理微信');void refresh()}
        else if(result.notice)toast.warning('微信尚未连接',{description:result.notice})
      }catch(error){if(!stopped)report(error)}
    })
    return()=>{stopped=true;clearInterval(timer)}
  },[load,update,refresh])
  const groups=useMemo<Group[]>(()=>sessions.filter(s=>s.is_group).map(s=>({groupCode:s.username,groupName:s.display,memberCount:0,maxMember:0,remark:s.summary,avatarUrl:s.avatar_url||''})),[sessions])
  const friends=useMemo<Friend[]>(()=>sessions.filter(s=>!s.is_group).map(s=>({uid:s.username,uin:0,nick:s.display,remark:s.display,avatarUrl:s.avatar_url||'',sessionCategory:s.category||'friend',chatType:1})),[sessions])
  const recent=useMemo(()=>Object.fromEntries(sessions.flatMap(s=>{
    const date=new Date(Number(s.last_time)*1000)
    return Number.isFinite(date.getTime())?[[s.username,date.toISOString()]]:[]
  })),[sessions])
  const chooseItems=(ids:string[])=>{const wanted=new Set(ids);setExportItems(sessions.filter(s=>wanted.has(s.username)).map(s=>({id:s.username,name:s.display,type:s.is_group?'group':'friend',chatType:s.is_group?2:1,peerUid:s.username,avatarUrl:s.avatar_url||''})));setExportOpen(true)}
  const getBatch=()=>chooseItems([...selected].map(id=>id.replace(/^(group|friend)_/,'')))
  const openConnect=async()=>{const epoch=libraryEpoch.current;setConnectOpen(true);setDetected(null);setConnectError('');try{const data=await call('detect');if(epoch!==libraryEpoch.current)return;setDetected(data);setAccount(data.accounts.find((a:any)=>a.connected)?.id||data.accounts[0]?.id||'')}catch(e){if(epoch===libraryEpoch.current)setConnectError((e as Error).message)}}
  const resetSessionView=()=>{connectJob.current='';autoConnect.current=false;setSessions([]);setLoading(false);setSelected(new Set());setBatch(false);setPreview(null);setPreviewOpen(false);setExportItems([]);setExportOpen(false)}
  const importArchive=async()=>{changingLibrary.current=true;libraryEpoch.current++;try{const data=await call('choose_archive');if(!data.cancelled){resetSessionView();update(data);await load();setTab('sessions')}}catch(e){report(e)}finally{changingLibrary.current=false}}
  const demo=async()=>{if(changingLibrary.current)return;changingLibrary.current=true;libraryEpoch.current++;const returning=!!stateRef.current?.library?.demo;if(!returning)demoReturnTab.current=tab;try{const data=await call(returning?'return_from_demo':'demo');resetSessionView();update(data);if(data.library)await load();setTab(returning?demoReturnTab.current:'sessions')}catch(e){report(e)}finally{changingLibrary.current=false}}
  const startConnect=async()=>{const epoch=libraryEpoch.current;setConnecting(true);try{const result=await call('connect',{accountId:account});if(epoch!==libraryEpoch.current)return;connectJob.current=result.job;autoConnect.current=false;setConnectOpen(false);setTab('tasks');void refresh()}catch(e){if(epoch===libraryEpoch.current)setConnectError((e as Error).message)}finally{setConnecting(false)}}
  const reconnect=async()=>{const epoch=libraryEpoch.current;setConnecting(true);try{const result=await call('reconnect');if(epoch!==libraryEpoch.current)return;if(result.job){connectJob.current=result.job;autoConnect.current=false;toast.info('正在重新连接并整理微信');void refresh()}else await load()}catch(e){if(epoch===libraryEpoch.current)report(e)}finally{setConnecting(false)}}
  const logout=async()=>{changingLibrary.current=true;libraryEpoch.current++;try{const data=await call('logout');resetSessionView();update(data);setDetected(null);setAccount('');setConnectError('');setConnectOpen(false);setTab('overview');toast.success('已退出登录',{description:'任务记录和已导出的文件已保留。'})}catch(e){report(e)}finally{changingLibrary.current=false}}
  const toggle=(type:string,id:string)=>setSelected(prev=>{const result=new Set(prev),key=`${type}_${id}`;result.has(key)?result.delete(key):result.add(key);return result})
  const selectMany=(ids:Set<string>,mode:'add'|'remove')=>setSelected(prev=>{const result=new Set(prev);ids.forEach(id=>mode==='add'?result.add(id):result.delete(id));return result})
  const exportChats=async(config:BatchExportConfig)=>{try{let start='',end='';if(config.timeRange==='recent'){const d=new Date();d.setMonth(d.getMonth()-3);start=d.toISOString().slice(0,10)}if(config.timeRange==='custom'){start=config.customStartDate||'';end=config.customEndDate||''}await call('export',{sessions:exportItems.map(i=>i.id),format:config.format.toLowerCase(),start,end,query:config.keywords,includeSystemMessages:config.includeSystemMessages,quickExport:config.quickExport,includeAvatars:config.includeAvatars,skipDownloadResourceTypes:config.skipDownloadResourceTypes||[],archiveZip:config.archiveZip,archiveEncrypt:config.archiveEncrypt,archivePassword:config.archivePassword});setExportOpen(false);setTab('tasks');await refresh()}catch(e){report(e);throw e}}
  const theme=async(event:MouseEvent<HTMLButtonElement>)=>{
    if(appearanceBusy.current)return
    appearanceBusy.current=true;setChangingAppearance(true)
    const source=event.currentTarget
    try{
      const data=await call('settings',{theme:stateRef.current.theme==='dark'?'light':'dark'})
      await revealTheme(source,()=>update(data,true))
    }catch(e){report(e)}finally{appearanceBusy.current=false;setChangingAppearance(false)}
  }
  const accent=async(id:string)=>{
    if(appearanceBusy.current)return
    appearanceBusy.current=true;setChangingAppearance(true)
    try{update(await call('settings',{accent:id}),true)}catch(e){report(e)}finally{appearanceBusy.current=false;setChangingAppearance(false)}
  }
  const nativeAction=async(action:string,payload:any={})=>{try{const data=await call(action,payload);if((action==='choose_output'||action==='delete_task')&&!data.cancelled)update(data);if(action==='choose_diagnostics'&&!data.cancelled)toast.success('诊断信息已导出')}catch(e){report(e)}}
  // Test hooks exercise this same page and bridge, using only fictional records.
  window.__wce={setTab,demo,chooseItems,closePreview:()=>setPreviewOpen(false),showPreview:(id:string)=>{const s=sessions.find(s=>s.username===id);if(s){setPreview({type:s.is_group?'group':'friend',id,name:s.display,peer:{chatType:s.is_group?2:1,peerUid:id}});setPreviewOpen(true)}},sessions,state}
  if(!state)return <div className="h-screen grid place-items-center bg-background"><Loader size={24}/></div>
  const tasks=state.tasks,exports=tasks.filter((t:any)=>t.kind==='export'),stats={total:exports.length,running:exports.filter((t:any)=>active(t.status)).length,completed:exports.filter((t:any)=>t.status==='completed').length}
  const overviewStats=state.library?stats:{total:0,running:0,completed:0}
  const connectBusy=connecting||tasks.some((t:any)=>t.kind==='connect'&&active(t.status))
  const nav=[{id:'brief',label:'每日简报',icon:FileText},{id:'overview',label:'概览',icon:Activity},{id:'sessions',label:'会话',icon:MessageCircle},{id:'tasks',label:'任务',icon:Zap},{id:'stickers',label:'表情包',icon:Sticker},{id:'moments',label:'朋友圈',icon:Images}]
  const bottom=[{id:'settings',label:'设置',icon:Settings},{id:'about',label:'关于',icon:HelpCircle}]
  const navButton=(item:any)=>{const Icon=item.icon;return <motion.button key={item.id} id={`nav-${item.id}`} onClick={()=>setTab(item.id)} className={`w-full flex items-center gap-2.5 px-2 py-[6px] text-[14px] font-medium rounded-lg transition-colors ${tab===item.id?'text-primary bg-primary/10':'text-muted-foreground hover:text-foreground hover:bg-black/[0.04] dark:hover:bg-white/[0.04]'}`}><Icon className="w-[18px] h-[18px] flex-shrink-0"/><span className="truncate">{item.label}</span>{item.id==='tasks'&&stats.running>0&&<Loader size={12} className="ml-auto text-blue-600"/>}</motion.button>}
  const deleteTask=(t:any)=>toast.info('删除这条任务记录？',{description:t.title+' · 导出的文件仍会保留',duration:Infinity,action:{label:'删除记录',onClick:()=>nativeAction('delete_task',{id:t.id})}})
  const taskRow=(t:any,compact=false)=><TaskRow key={t.id} task={t} compact={compact} onAction={(action,id)=>nativeAction(action,{id})} onDelete={deleteTask}/>
  return <TooltipProvider><div className="flex h-screen w-full bg-background font-sans overflow-hidden"><AnimatePresence initial={false}>{sidebar&&<motion.div initial={{width:0,opacity:0}} animate={{width:208,opacity:1}} exit={{width:0,opacity:0}} className="h-full flex flex-col flex-shrink-0 overflow-hidden select-none relative"><div className="w-[208px] h-full flex flex-col"><button onClick={openConnect} className="flex items-center px-4 h-14 flex-shrink-0 text-left"><div className="flex items-center gap-2.5 min-w-0"><Avatar className="w-7 h-7 flex-shrink-0 rounded-full">{state.library?.profile?.avatar_url&&<AvatarImage src={state.library.profile.avatar_url}/>}<AvatarFallback className="text-[11px] rounded-full">{state.library?.profile?.name?.[0]||<img src="./icon.svg" className="w-full h-full" alt="Moect"/>}</AvatarFallback></Avatar><span className="text-[14px] font-semibold text-foreground truncate">{state.library?.name||'Moect'}</span></div></button><div className="flex-1 overflow-y-auto px-2 py-1"><div className="space-y-0.5">{nav.map(navButton)}</div><div className="mt-4 mb-1 px-2"><span className="text-[12px] font-medium text-muted-foreground/60 uppercase tracking-wider">系统</span></div><div className="space-y-0.5">{bottom.map(navButton)}</div></div><div className="flex-shrink-0 px-2 pb-2 space-y-1"><div className="flex items-center justify-center px-2 py-1"><button onClick={()=>nativeAction('source',{name:'project'})} className="inline-flex items-center gap-1.5 h-8 px-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-black/[0.04] dark:hover:bg-white/[0.04] transition-colors"><ExternalLink className="w-4 h-4"/><span className="text-[13px]">Moect GitHub</span></button></div><div className="sidebar-actions" role="group" aria-label="更新、公告与外观"><div id="sidebar-online"/><button onClick={theme} disabled={changingAppearance} aria-busy={changingAppearance} title="切换主题" className="sidebar-theme-shortcut inline-flex h-9 w-9 items-center justify-center rounded-full bg-transparent hover:bg-accent transition-colors"><ThemeToggleIcon dark={state.theme==='dark'}/></button></div></div></div></motion.div>}</AnimatePresence><div className="flex-1 flex flex-col overflow-hidden sm:m-2 sm:rounded-xl sm:border sm:border-black/[0.05] sm:shadow-[0_2px_8px_rgba(0,0,0,0.015)] bg-card dark:border-white/[0.06]"><div className="flex flex-shrink-0 items-center justify-between border-b border-black/[0.06] dark:border-white/[0.06] px-4 h-12"><div className="flex items-center gap-2 text-[14px]"><button onClick={()=>setSidebar(!sidebar)} aria-label="切换侧栏" className="p-1 -ml-1 rounded-full text-muted-foreground/60 hover:text-muted-foreground hover:bg-black/[0.04] dark:hover:bg-white/[0.04] transition-colors">{sidebar?<PanelLeftClose className="w-4 h-4"/>:<PanelLeftOpen className="w-4 h-4"/>}</button><span className="font-medium text-muted-foreground">Moect</span><span className="text-muted-foreground/30">/</span><span className="font-semibold text-foreground">{titles[tab]}</span></div><WindowDragRegion/><div className="flex items-center gap-2">{state.library?.demo&&<Badge variant="secondary" className="text-xs rounded-full">演示数据</Badge>}{tab==='sessions'&&<>{batch&&<Button size="sm" variant="outline" disabled={!selected.size} onClick={async()=>{try{await call('liubai',{operation:'save_scope',selected:[...selected].map(id=>id.replace(/^(group|friend)_/,''))});toast.success('已保存简报会话范围');setTab('brief')}catch(e){report(e)}}}>保存到简报（{selected.size}）</Button>}<Button size="sm" variant="ghost" className="h-8 text-[13px] rounded-full px-2.5" disabled={!state.library?.live||tasks.some((t:any)=>['connect','refresh_assets','recover_images'].includes(t.kind)&&active(t.status))} onClick={()=>{void nativeAction('recover_images')}}>恢复图片读取</Button><Button size="sm" variant="ghost" className="h-8 text-[13px] rounded-full px-2" onClick={reconnect} title="重新连接并整理微信" disabled={connecting||tasks.some((t:any)=>['connect','refresh_assets','recover_images'].includes(t.kind)&&active(t.status))||loading||!state.library}>{loading?<Loader size={16}/>:<RefreshCw className="w-4 h-4"/>}</Button><Button size="sm" variant="ghost" className="h-8 text-[13px] rounded-full px-2.5" onClick={()=>{setBatch(!batch);setSelected(new Set())}}>批量选择</Button></>}{tab==='tasks'&&<Button size="sm" className="h-8 text-[13px] rounded-full px-2.5" onClick={()=>setTab('sessions')}>新建任务</Button>}<Button size="sm" variant="ghost" className="h-8 text-[13px] rounded-full px-2.5" onClick={openConnect}>连接微信</Button><WindowControls/></div></div><div className={tab==='brief'?"flex-1 min-h-0 overflow-hidden":"flex-1 overflow-auto"}>
  {tab==='brief'&&<PersonalBrief onSelectSessions={()=>{setTab('sessions');setBatch(true)}}/>}
  {tab==='overview'&&<div className="p-6 space-y-6"><div className="flex items-center gap-4"><Avatar className="w-12 h-12 rounded-full">{state.library?.profile?.avatar_url&&<AvatarImage src={state.library.profile.avatar_url}/>}<AvatarFallback className="rounded-full text-sm">{state.library?.profile?.name?.[0]||<img src="./icon.svg" className="w-full h-full" alt="Moect"/>}</AvatarFallback></Avatar><div><h2 className="text-lg font-semibold text-foreground">{state.library?.name||'Moect'}</h2><div className="flex items-center gap-2 mt-0.5 text-sm text-muted-foreground"><span>{state.library?(state.library.demo?'演示归档 · 虚构数据':`微信 ${state.library.profile?.alias||state.library.profile?.wxid||'本地归档'}`):'本地模式 · 尚未连接微信'}</span></div></div></div><div className="grid grid-cols-3 gap-4"><div className="rounded-xl p-4"><div className="text-sm text-muted-foreground">导出任务</div><div className="text-2xl font-semibold tracking-tight mt-1">{overviewStats.total}</div><div className="text-xs text-muted-foreground/60 mt-1">进行中 {overviewStats.running} · 完成 {overviewStats.completed}</div></div><div className="rounded-xl p-4"><div className="text-sm text-muted-foreground">群组</div><div className="text-2xl font-semibold tracking-tight mt-1">{groups.length}</div></div><div className="rounded-xl p-4"><div className="text-sm text-muted-foreground">好友</div><div className="text-2xl font-semibold tracking-tight mt-1">{friends.filter(friend=>!friend.sessionCategory||friend.sessionCategory==='friend').length}</div></div></div><div className="flex items-center gap-3"><Button onClick={()=>state.library?setTab('sessions'):openConnect()} className="rounded-full">{state.library?'新建任务':'连接微信'}</Button><Button variant="outline" onClick={()=>setTab('sessions')} className="rounded-full">浏览会话</Button><Button variant="outline" onClick={()=>setTab('tasks')} className="rounded-full">查看任务</Button></div>{!state.library&&<div className="rounded-xl bg-card p-6 space-y-4"><div className="space-y-1.5"><h3 className="text-[15px] font-semibold">连接微信，开始导出</h3><p className="text-[13px] text-muted-foreground/80 leading-relaxed">保持电脑微信登录，然后点击“连接微信”。也可以打开已有的解密备份，或体验演示数据。</p></div><Button variant="outline" className="rounded-full" onClick={demo}>体验演示</Button></div>}{state.library&&exports.length>0&&<div><div className="flex items-center justify-between mb-3"><h3 className="text-sm font-medium">最近任务</h3><button onClick={()=>setTab('tasks')} className="text-sm text-muted-foreground hover:text-foreground">查看全部</button></div><div className="rounded-xl divide-y divide-black/[0.04] dark:divide-white/[0.04] overflow-hidden">{exports.slice(0,5).map((t:any)=>taskRow(t,true))}</div></div>}</div>}
  {tab==='sessions'&&<div className="px-4 pt-2 pb-5 space-y-2">{state.library?<SessionList sortPreference={state.session_sort} onSortPreferenceChange={saveSessionSort} groups={groups} friends={friends} batchMode={batch} selectedItems={selected} recentActivityMap={recent} onToggleBatchMode={()=>setBatch(!batch)} onToggleItem={toggle} onSelectMany={selectMany} onSelectAll={ids=>selectMany(ids||new Set(sessions.map(s=>(s.is_group?'group_':'friend_')+s.username)),'add')} onClearSelection={()=>setSelected(new Set())} onOpenBatchExportDialog={getBatch} onOpenTaskWizard={preset=>chooseItems([preset.peerUid])} onPreviewChat={(type,id,name,peer)=>{setPreview({type,id,name,peer});setPreviewOpen(true)}}/>:<div className="rounded-xl bg-card p-6 space-y-4"><h3 className="text-[15px] font-semibold">尚未连接微信归档</h3><p className="text-[13px] text-muted-foreground/80 leading-relaxed">连接已登录的微信，或打开已有的解密备份。</p><div className="flex gap-2"><Button onClick={openConnect} className="rounded-full">连接微信</Button><Button onClick={importArchive} variant="outline" className="rounded-full">打开备份</Button><Button onClick={demo} variant="ghost" className="rounded-full">演示模式</Button></div></div>}</div>}
  {!onboarding.open&&(tab==='stickers'||tab==='moments')&&<AccountAssetsPage key={tab} kind={tab} library={state.library} busy={tasks.some((t:any)=>['connect','refresh_assets','recover_images'].includes(t.kind)&&active(t.status))} onConnect={openConnect} onExportQueued={()=>{setTab('tasks');void refresh()}} onRefreshQueued={()=>void refresh()}/>}
  {tab==='tasks'&&<TasksPage tasks={tasks} onAction={(action,id)=>void nativeAction(action,{id})} onDelete={deleteTask} onChanged={update}/>}
  {tab==='settings'&&<div className="settings-page p-6 space-y-5"><h2 className="text-lg font-semibold">设置</h2><section className="space-y-4"><h3 className="text-[14px] font-medium">导出位置</h3><div className="flex items-center gap-3"><input className={PILL+' flex-1'} readOnly value={state.export_dir}/><Button size="sm" variant="outline" className="rounded-full" onClick={()=>nativeAction('choose_output')}>选择文件夹</Button></div></section><StoragePage state={state} onChanged={async()=>{const next=await call('state');update(next);if(!next.library)resetSessionView();else if(!next.storage_busy)await load()}} onError={report}/><section className="space-y-4"><h3 className="text-[14px] font-medium">数据来源</h3><div className="flex flex-wrap gap-3"><Button className="rounded-full" onClick={openConnect}>连接微信</Button><Button variant="outline" className="rounded-full" onClick={importArchive}>打开已有备份</Button><Button variant="ghost" className="rounded-full" onClick={demo}>{state.library?.demo?'返回':'演示模式'}</Button></div></section><section className="space-y-4"><h3 className="text-[14px] font-medium">外观</h3><div className="space-y-3"><div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 max-w-[600px]">{state.accents?.map((item:any)=><button key={item.id} type="button" disabled={changingAppearance} onClick={()=>accent(item.id)} aria-pressed={state.accent===item.id} className={`rounded-xl border px-3 py-2.5 flex items-center justify-center gap-2.5 text-[13px] transition-colors ${state.accent===item.id?'border-primary/40 bg-primary/5':'border-border/60 hover:bg-accent/40'}`}><span className="w-6 h-6 rounded-full shrink-0 grid place-items-center text-white" style={{backgroundColor:item.color}}>{state.accent===item.id&&<Check className="w-3.5 h-3.5"/>}</span>{item.name}</button>)}</div></div><Button variant="outline" className="rounded-full disabled:opacity-100" disabled={changingAppearance} aria-busy={changingAppearance} onClick={theme}>{state.theme==='dark'?'切换到浅色':'切换到深色'}</Button></section><section className="space-y-3"><h3 className="text-[14px] font-medium">使用帮助</h3><div className="flex flex-wrap gap-2"><Button variant="outline" className="rounded-full" onClick={onboarding.show}>重新播放入门教程</Button><Button variant="outline" className="rounded-full" onClick={()=>nativeAction('choose_diagnostics')}>导出诊断信息</Button></div></section></div>}
  {tab==='about'&&<div className="about-page"><div className="p-6 space-y-6 max-w-3xl"><h2 className="text-lg font-semibold">Moect</h2><p className="text-[13px] text-muted-foreground leading-relaxed">Moect 将微信会话、媒体资源、合并转发与离线归档整合为本地桌面软件。</p><div className="text-[13px] text-muted-foreground leading-relaxed space-y-3"><p>版本 {state.version} · GPL-3.0 衍生软件。</p><p>支持 HTML / JSON / TXT / CSV / JSONL / Markdown / Excel，可选 ZIP 压缩与 AES 加密。头像和本地附件随导出保存；链接、小程序保留卡片信息。未下载或无法解密的附件会显示状态说明。</p><p>面向 Windows 微信 4.x。微信更新可能影响兼容性，媒体可用性取决于本地缓存与读取凭据。</p><p>界面使用 HarmonyOS Sans SC（鸿蒙字体）。Copyright 2021 Huawei Device Co., Ltd.，字体按 HarmonyOS Sans Fonts License Agreement 随附。</p><p>聊天数据在本机处理，媒体和头像按需从来源下载。启动时会联网检查公告与新版本；公告已读状态、密钥与快照均保存在本机。</p></div><div className="flex flex-wrap gap-3">{[['article','微信文章 MCP'],['sonora','微信提示音'],['wechat','WeChatMsg'],['wx4','4.x 适配层'],['project','Moect GitHub']].map(([name,label])=><Button key={name} variant="outline" className="rounded-full" onClick={()=>nativeAction('source',{name})}>{label}<ExternalLink className="w-3.5 h-3.5 ml-2"/></Button>)}</div></div><footer className="about-signature"><button type="button" onClick={()=>nativeAction('source',{name:'author'})}>Designed By MOFAN</button></footer></div>}
  </div></div></div>
  {(previewOpen||exportOpen)&&<div className="fixed top-0 left-0 right-0 h-12 z-[200] bg-background flex items-center pr-4"><WindowDragRegion/><WindowControls/></div>}
  {previewOpen&&<MessagePreviewModal open={previewOpen} onClose={()=>setPreviewOpen(false)} chat={preview} onExport={()=>{if(preview)chooseItems([preview.id])}}/>}
  <BatchExportDialog open={exportOpen} onOpenChange={setExportOpen} items={exportItems} onExport={exportChats}/>
  <Dialog open={connectOpen} onOpenChange={setConnectOpen}><DialogContent className="p-8 max-w-lg">
    <DialogTitle className="text-lg font-semibold mb-2">连接微信</DialogTitle>
    <p className="text-[13px] text-muted-foreground mb-6 leading-relaxed">保持电脑微信已登录，软件会自动获取读取凭据、整理快照并载入会话。</p>
    {!detected&&!connectError?<div className="flex justify-center py-8"><Loader size={20}/></div>:<div className="space-y-4">
      <p className="text-[13px] text-muted-foreground">{detected?.running?'已检测到运行中的微信，请确认已登录':'请先打开并登录电脑微信'}</p>
      {detected?.accounts.map((a:any)=><div key={a.id} className="flex items-center gap-2 p-3 rounded-xl bg-black/[0.03] dark:bg-white/[0.04] text-sm">
        <label className="flex flex-1 min-w-0 items-center gap-3 cursor-pointer"><input type="radio" name="wechat-account" checked={a.id===account} onChange={()=>setAccount(a.id)}/>
          <Avatar className="w-8 h-8 shrink-0 rounded-full">{a.avatar_url&&<AvatarImage src={a.avatar_url}/>}<AvatarFallback>{a.name?.[0]||'W'}</AvatarFallback></Avatar>
          <span className="min-w-0"><span className="block font-medium truncate">{a.name}</span><span className="block text-xs text-muted-foreground truncate">微信 {a.wxid}</span></span>
        </label>
        {a.connected&&<Button size="sm" variant="ghost" className="shrink-0 rounded-full text-primary" title="断开 Moect 的连接，保留任务和导出文件" onClick={logout}><LogOut className="w-3.5 h-3.5 mr-1.5"/>退出登录</Button>}
      </div>)}
      <button className="text-xs text-muted-foreground hover:text-foreground" onClick={async()=>{const epoch=libraryEpoch.current;try{const a=await call('choose_storage');if(epoch!==libraryEpoch.current)return;if(!a.cancelled){const accounts=a.accounts||[a];const added=new Set(accounts.map((item:any)=>item.id));setDetected({...detected,accounts:[...(detected?.accounts||[]).filter((item:any)=>!added.has(item.id)),...accounts]});setAccount(accounts[0]?.id||'')}}catch(e){setConnectError((e as Error).message)}}}>手动选择数据目录</button>
    </div>}
    {connectError&&<p className="text-sm text-red-600 my-4">{connectError}</p>}
    <div className="flex items-center justify-end gap-2 mt-7">
      {state.library&&!detected?.accounts.some((a:any)=>a.connected)&&<Button variant="ghost" className="rounded-full text-primary mr-auto" onClick={logout}><LogOut className="w-4 h-4 mr-1.5"/>退出登录</Button>}
      <Button variant="ghost" className="rounded-full" onClick={openConnect}>重新检测</Button>
      <span className="mr-auto text-xs text-muted-foreground">{connectBusy?'正在整理，请等待完成':!detected?.running?'请先打开电脑微信并登录':!account?'请先选择账号或手动选择数据目录':''}</span><Button className="rounded-full" disabled={!account||!detected?.running||connectBusy} onClick={startConnect}>{connectBusy?'正在连接并整理':'连接并整理'}</Button>
    </div>
  </DialogContent></Dialog>
  <AppTutorial open={onboarding.open} onClose={onboarding.close}/>
  {onboarding.checked&&!onboarding.open&&<Toaster position="bottom-right" theme="macos"/>}</TooltipProvider>
}
