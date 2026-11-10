import tempfile, unittest
from unittest.mock import patch, Mock
import api, automation_store, analysis_checkpoint, scope_policy, auto_startup

class DailyTests(unittest.TestCase):
 def test_daily_is_deduped_and_filtered_by_scope(self):
  with tempfile.TemporaryDirectory() as d:
   items=[{'category':'task','title':'回复','sources':[{'conversationId':'a','serverId':'1','at':1700000000,'text':'回复'}]}, {'category':'group','title':'重点','sources':[{'conversationId':'b','serverId':'2','at':1700000000,'text':'重点'}]}]
   automation_store.merge_daily(d,items,lambda b:b,lambda b:b);automation_store.merge_daily(d,items,lambda b:b,lambda b:b)
   from datetime import datetime
   date=datetime.fromtimestamp(1700000000).strftime('%Y-%m-%d')
   self.assertEqual(len(automation_store.read_daily(d,date,{'a','b'},lambda b:b)),2)
   self.assertEqual([x['title'] for x in automation_store.read_daily(d,date,{'a'},lambda b:b)],['回复'])
   with self.assertRaises(ValueError):automation_store.read_daily(d,'../../wrong',{'a'},lambda b:b)

class AutomationTests(unittest.TestCase):
 def test_sync_then_job_then_success_watermark(self):
  now=1760000000;cfg={'baseUrl':'https://test','model':'m','apiKey':'s'}
  with tempfile.TemporaryDirectory() as root,patch.object(api,'CACHE_ROOT',root),patch.object(api,'JOB_THREAD',None),patch.object(api,'AUTO_REFRESH',None),patch.object(api.secrets_store,'protect',lambda b:b),patch.object(api.secrets_store,'unprotect',lambda b:b),patch.object(api.agent,'load_config',return_value=cfg),patch.dict(api.STATE,{'phase':'ready','snapshot':root+'\\acct','account':{'dirName':'acct','dbStorage':'fake'},'stats':{'databases':2,'decrypted':2}},clear=False):
   directory=api._auto_directory('acct');scope_policy.save_scope(directory,{'chat'},lambda b:b)
   automation_store.save(root,api.AUTO_INDEX,{'account':'acct'},lambda b:b)
   automation_store.save(directory,api.AUTO_NAME,{'enabled':True,'state':'waiting','nextRunAt':now},lambda b:b)
   with patch.object(api,'start_connect',return_value=True) as connect:
    api._automation_tick(now);self.assertEqual(connect.call_count,1)
   api._automation_tick(now+15)
   state=automation_store.load(directory,api.AUTO_NAME,lambda b:b,{})
   self.assertEqual(state['state'],'pending');self.assertNotIn('lastSuccessEnd',state)
   self.assertEqual(state['pending']['end'],now)
   with patch.object(api,'start_analysis_job',return_value={'id':'j'}) as start:
    api._automation_tick(now+30);self.assertEqual(start.call_count,1)
   state=automation_store.load(directory,api.AUTO_NAME,lambda b:b,{})
   self.assertEqual(state['state'],'running');self.assertNotIn('lastSuccessEnd',state)
   analysis_checkpoint.save(directory,{'version':1,'id':'j','status':'complete'},lambda b:b)
   api._automation_tick(now+45)
   state=automation_store.load(directory,api.AUTO_NAME,lambda b:b,{})
   self.assertEqual(state['lastSuccessEnd'],now);self.assertIsNone(state['pending'])
 def test_failure_never_advances_watermark(self):
  now=1760000000;cfg={'baseUrl':'https://test','model':'m'}
  with tempfile.TemporaryDirectory() as root,patch.object(api,'CACHE_ROOT',root),patch.object(api,'JOB_THREAD',None),patch.object(api.auto_startup,'configure'),patch.object(api.secrets_store,'protect',lambda b:b),patch.object(api.secrets_store,'unprotect',lambda b:b),patch.object(api.agent,'load_config',return_value=cfg),patch.dict(api.STATE,{'phase':'ready','snapshot':root+'\\acct','account':{'dirName':'acct'},'stats':{}},clear=False):
   directory=api._auto_directory('acct');scope_policy.save_scope(directory,{'chat'},lambda b:b)
   automation_store.save(root,api.AUTO_INDEX,{'account':'acct'},lambda b:b)
   pending={'start':100,'end':200,'jobId':'j','scopeSignature':api._scope_signature({'chat'}),'configSignature':api._job_signature(cfg)}
   automation_store.save(directory,api.AUTO_NAME,{'enabled':True,'state':'running','lastSuccessEnd':100,'pending':pending},lambda b:b)
   analysis_checkpoint.save(directory,{'version':1,'id':'j','status':'failed','error':'timeout'},lambda b:b)
   api._automation_tick(now)
   state=automation_store.load(directory,api.AUTO_NAME,lambda b:b,{})
   self.assertEqual(state['lastSuccessEnd'],100);self.assertEqual(state['pending']['end'],200);self.assertEqual(state['state'],'retry_wait')
class SchedulerRegistrationTests(unittest.TestCase):
 def test_registration_is_user_scoped_and_contains_no_credentials(self):
  with patch.object(auto_startup.os.path,'isfile',return_value=True), patch.object(auto_startup.subprocess,'run',return_value=Mock(returncode=0)) as run:
   auto_startup.configure(True)
  args=run.call_args.args[0]
  self.assertIn('/Create',args);self.assertIn('HOURLY',args)
  command=args[args.index('/TR')+1]
  self.assertIn('pythonw.exe',command);self.assertIn('api.py',command)
  self.assertNotIn('apiKey',command)

class RecoveryTests(unittest.TestCase):
 def test_scope_change_disables_pending_run_without_advancing(self):
  now=1760000000;cfg={'baseUrl':'https://test','model':'m'}
  with tempfile.TemporaryDirectory() as root,patch.object(api,'CACHE_ROOT',root),patch.object(api,'JOB_THREAD',None),patch.object(api.auto_startup,'configure'),patch.object(api.secrets_store,'protect',lambda b:b),patch.object(api.secrets_store,'unprotect',lambda b:b),patch.object(api.agent,'load_config',return_value=cfg),patch.dict(api.STATE,{'phase':'ready','snapshot':root+'\\acct','account':{'dirName':'acct'},'stats':{}},clear=False):
   directory=api._auto_directory('acct');scope_policy.save_scope(directory,{'new'},lambda b:b)
   automation_store.save(root,api.AUTO_INDEX,{'account':'acct'},lambda b:b)
   pending={'start':100,'end':200,'jobId':'j','scopeSignature':api._scope_signature({'old'}),'configSignature':api._job_signature(cfg)}
   automation_store.save(directory,api.AUTO_NAME,{'enabled':True,'state':'running','lastSuccessEnd':100,'pending':pending},lambda b:b)
   api._automation_tick(now)
   state=automation_store.load(directory,api.AUTO_NAME,lambda b:b,{})
   self.assertFalse(state['enabled']);self.assertEqual(state['lastSuccessEnd'],100);self.assertEqual(state['pending']['end'],200)
 def test_incomplete_snapshot_never_advances(self):
  now=1760000000;cfg={'baseUrl':'https://test','model':'m'}
  with tempfile.TemporaryDirectory() as root,patch.object(api,'CACHE_ROOT',root),patch.object(api,'JOB_THREAD',None),patch.object(api,'AUTO_REFRESH',now),patch.object(api.secrets_store,'protect',lambda b:b),patch.object(api.secrets_store,'unprotect',lambda b:b),patch.object(api.agent,'load_config',return_value=cfg),patch.dict(api.STATE,{'phase':'ready','snapshot':root+'\\acct','account':{'dirName':'acct'},'stats':{'databases':4,'decrypted':3}},clear=False):
   directory=api._auto_directory('acct');scope_policy.save_scope(directory,{'chat'},lambda b:b)
   automation_store.save(root,api.AUTO_INDEX,{'account':'acct'},lambda b:b)
   automation_store.save(directory,api.AUTO_NAME,{'enabled':True,'state':'syncing','syncStart':now,'lastSuccessEnd':100},lambda b:b)
   api._automation_tick(now+1)
   state=automation_store.load(directory,api.AUTO_NAME,lambda b:b,{})
   self.assertEqual(state['lastSuccessEnd'],100);self.assertEqual(state['state'],'retry_wait')

class SummaryTests(unittest.TestCase):
 def test_model_summary_drops_uncited_points_and_keeps_sources(self):
  import agent,json
  entry={'title':'讨论项目','summary':'对方希望本周沟通','sources':[{'conversationId':'c','serverId':'1','at':1700000000,'text':'本周沟通'}]}
  raw=[{'kind':'focus','text':'本周沟通项目','evidence':['1']},{'kind':'task','text':'编造','evidence':['9']}]
  with patch.object(agent,'_post_json',return_value={'choices':[{'message':{'content':json.dumps(raw,ensure_ascii=False)}}]}):
   report=agent.summarize_daily([entry],{'baseUrl':'https://example','model':'test'},'2026-11-09')
  self.assertTrue(report['ok']);self.assertEqual(len(report['points']),1)
  self.assertEqual(report['points'][0]['sources'][0]['conversationId'],'c')
 def test_summary_hides_point_if_any_source_excluded(self):
  with tempfile.TemporaryDirectory() as d:
   point={'text':'综合结论','sources':[{'conversationId':'a'},{'conversationId':'b'}]}
   automation_store.save_summary(d,'2026-11-09',{'points':[point]},lambda b:b)
   self.assertEqual(automation_store.read_summary(d,'2026-11-09',{'a'},lambda b:b)['points'],[])
   self.assertEqual(len(automation_store.read_summary(d,'2026-11-09',{'a','b'},lambda b:b)['points']),1)

class SummaryRecoveryTests(unittest.TestCase):
 def test_summary_failure_resumes_without_reprocessing_messages(self):
  import time
  now=int(time.time());date=__import__('datetime').datetime.fromtimestamp(now).strftime('%Y-%m-%d')
  cfg={'baseUrl':'https://test','model':'m'}
  class Reader:
   def name_of(self,username):return '我'
   def messages_page(self,*args,**kwargs):raise AssertionError('should not reread finished messages')
  r=Reader();ref={'conversationId':'chat','conversationName':'朋友','serverId':'1','at':now,'text':'请回复','timeText':'今天'}
  item={'category':'task','title':'回复','summary':'回复朋友','sources':[ref]}
  with tempfile.TemporaryDirectory() as d,patch.object(api,'READER',r),patch.object(api,'JOB_CANCEL',__import__('threading').Event()),patch.dict(api.STATE,{'phase':'ready','snapshot':d,'account':{'dirName':'acct','wxid':'me'}},clear=False),patch.object(api.secrets_store,'protect',lambda b:b),patch.object(api.secrets_store,'unprotect',lambda b:b),patch.object(api.agent,'load_config',return_value=cfg):
   scope_policy.save_scope(d,{'chat'},lambda b:b);automation_store.merge_daily(d,[item],lambda b:b,lambda b:b)
   job={'version':1,'id':'j','status':'running','selected':['chat'],'snapshotSignature':'','start':now-1,'end':now+10,'sessions':[{'username':'chat','display':'朋友','category':'direct','sortAt':now}],'sessionIndex':1,'sessionCount':1,'offset':0,'messagesProcessed':1,'batchesCompleted':1,'tasksAdded':1,'insights':[item]}
   with patch.object(api.agent,'summarize_daily',return_value={'ok':False,'error':'timeout'}):api._analysis_job_worker(d,r,{'dirName':'acct','wxid':'me'},cfg,job)
   saved=analysis_checkpoint.load(d,lambda b:b);self.assertEqual(saved['status'],'failed');self.assertEqual(saved['summaryIndex'],0)
   with patch.object(api.agent,'summarize_daily',return_value={'ok':True,'points':[{'text':'回复朋友','sources':[ref]}],'used':1,'total':1}):api._analysis_job_worker(d,r,{'dirName':'acct','wxid':'me'},cfg,saved)
   self.assertEqual(saved['status'],'complete')
   report=automation_store.read_summary(d,date,{'chat'},lambda b:b)
   self.assertEqual(report['scopeSignature'],api._scope_signature({'chat'}))

class FullAutomaticRunTests(unittest.TestCase):
 def test_tick_to_daily_report_with_fictional_data(self):
  from datetime import datetime
  now=1760000000;cfg={'baseUrl':'https://test','model':'m','apiKey':'fake'}
  class Reader:
   def name_of(self,username):return '我'
   def sessions(self,limit=10000):return [{'username':'chat','display':'测试联系人','category':'direct','sortAt':now-1}]
   def messages_page(self,username,start,end,offset,limit):return ([{'at':now-1,'text':'我会回复','senderId':'me','sender':'我','serverId':'1','timeText':'测试'}] if offset==0 else [])
  class Thread:
   def __init__(self,**kwargs):self.target=kwargs['target'];self.args=kwargs['args']
   def start(self):self.target(*self.args)
   def is_alive(self):return False
  r=Reader();ref={'conversationId':'chat','conversationName':'测试联系人','serverId':'1','at':now-1,'text':'我会回复','timeText':'测试'}
  item={'category':'task','title':'回复','summary':'回复测试联系人','assignee':'self','at':now-1,'sources':[ref]}
  summary={'ok':True,'points':[{'kind':'task','text':'需要回复','sources':[ref]}],'used':1,'total':1}
  with tempfile.TemporaryDirectory() as root,patch.object(api,'CACHE_ROOT',root),patch.object(api,'READER',r),patch.object(api,'JOB_THREAD',None),patch.object(api,'AUTO_REFRESH',None),patch.object(api.threading,'Thread',Thread),patch.object(api,'start_connect',return_value=True),patch.object(api.secrets_store,'protect',lambda b:b),patch.object(api.secrets_store,'unprotect',lambda b:b),patch.object(api.agent,'load_config',return_value=cfg),patch.object(api.agent,'analyze',return_value={'ok':True,'insights':[item]}),patch.object(api.agent,'summarize_daily',return_value=summary),patch.dict(api.STATE,{'phase':'ready','snapshot':root+'\\acct','account':{'dirName':'acct','wxid':'me'},'stats':{'databases':2,'decrypted':2}},clear=False):
   directory=api._auto_directory('acct');scope_policy.save_scope(directory,{'chat'},lambda b:b)
   automation_store.save(root,api.AUTO_INDEX,{'account':'acct'},lambda b:b)
   automation_store.save(directory,api.AUTO_NAME,{'enabled':True,'state':'waiting','nextRunAt':now},lambda b:b)
   api.JOB_CANCEL.clear()
   for offset in (0,15,30,45):api._automation_tick(now+offset)
   state=automation_store.load(directory,api.AUTO_NAME,lambda b:b,{})
   self.assertEqual(state['lastSuccessEnd'],now)
   date=datetime.fromtimestamp(now-1).strftime('%Y-%m-%d')
   report=automation_store.read_summary(directory,date,{'chat'},lambda b:b)
   self.assertEqual(report['points'][0]['text'],'需要回复')
   self.assertEqual(len(api.tasks_store.visible(api.tasks_store.load(directory,lambda b:b))),1)

class AtomicReplaceTests(unittest.TestCase):
 def test_windows_sharing_violation_is_retried(self):
  import atomic_replace
  error=PermissionError('busy');error.winerror=5
  with patch.object(atomic_replace.os,'replace',side_effect=[error,error,None]) as replace,patch.object(atomic_replace.time,'sleep'):
   atomic_replace.replace('temporary','destination')
  self.assertEqual(replace.call_count,3)

if __name__=='__main__':unittest.main()
