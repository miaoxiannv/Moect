import json, sqlite3, tempfile, threading, time, unittest
from unittest.mock import patch
import agent, api, reader, scope_policy, analysis_checkpoint, tasks_store
class PagingTests(unittest.TestCase):
 def test_equal_timestamps_across_shards(self):
  r=reader.WxReader.__new__(reader.WxReader);r._lock=threading.Lock();r._contacts={};r._msg_dbs=[]
  name=reader.msg_table_for('contact')
  for shard in range(2):
   con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row
   con.execute('CREATE TABLE "%s" (local_id integer,server_id integer,local_type integer,create_time integer,real_sender_id integer,message_content blob,compress_content blob)' % name)
   for i in range(30):con.execute('INSERT INTO "%s" VALUES (?,?,?,?,?,?,?)' % name,(i,shard*100+i+1,1,100+i//5,0,b'hello',None))
   r._msg_dbs.append(con)
  rows=[]
  for offset in range(0,60,7):rows.extend(r.messages_page('contact',100,200,offset,7))
  self.assertEqual(len(rows),60);self.assertEqual(len({x['serverId'] for x in rows}),60)
  self.assertEqual([x['at'] for x in rows],sorted(x['at'] for x in rows))
class OutputTests(unittest.TestCase):
 def test_due_and_task_update_need_evidence(self):
  class R:
   def sessions(self,limit):return [{'username':'a','display':'A','category':'direct','sortAt':200}]
   def messages(self,username,limit,start=None,end=None):return [{'at':150,'text':'我明天回复你','senderId':'me','sender':'我','serverId':'1','timeText':'t'}]
  raw=[{'category':'task','title':'回复','assignee':'self','dueText':'明天','evidence':['1']},{'category':'task','title':'错误时间','assignee':'other','dueText':'下周三','evidence':['1']},{'category':'task_update','taskId':'invented','evidence':['1']}]
  with patch.object(agent,'_post_json',return_value={'choices':[{'message':{'content':json.dumps(raw,ensure_ascii=False)}}]}):out=agent.analyze(R(),'me',{'baseUrl':'https://x','model':'test'},1,100,200)
  self.assertEqual(len(out['insights']),2);self.assertEqual(out['insights'][0]['dueText'],'明天');self.assertEqual(out['insights'][1]['dueText'],'')
class ResumeTests(unittest.TestCase):
 def test_resumes_at_failed_page(self):
  now=int(time.time())
  class R:
   def name_of(self,username):return '我'
   def sessions(self,limit=10000):return [{'username':'allowed','display':'好友','category':'direct','sortAt':now}]
   def messages_page(self,username,start,end,offset,limit):return [{'at':now+i,'text':'消息','senderId':'other','sender':'朋友','serverId':str(i),'timeText':'日期'} for i in range(50)][offset:offset+limit]
  cfg={'baseUrl':'https://test','model':'m','apiKey':'secret'};r=R();calls=[]
  def fake(batch,*args,**kwargs):
   msgs=batch.messages('allowed');calls.append(msgs[0]['serverId'])
   if len(calls)==2:return {'ok':False,'error':'temporary'}
   ref={'conversationId':'allowed','conversationName':'好友','serverId':msgs[0]['serverId'],'at':msgs[0]['at'],'timeText':'日期','text':'消息'}
   return {'ok':True,'insights':[{'category':'task','title':'回复','assignee':'self','sources':[ref]}]}
  with tempfile.TemporaryDirectory() as d, patch.object(api,'READER',r), patch.dict(api.STATE,{'phase':'ready','snapshot':d,'account':{'dirName':'acct','wxid':'me'}},clear=False), patch.object(api.secrets_store,'protect',lambda b:b),patch.object(api.secrets_store,'unprotect',lambda b:b),patch.object(api.agent,'load_config',return_value=cfg),patch.object(api.agent,'analyze',side_effect=fake),patch.object(api.agent,'summarize_daily',return_value={'ok':True,'points':[],'used':0,'total':0}):
   scope_policy.save_scope(d,{'allowed'},lambda b:b)
   job={'version':1,'id':'j','status':'running','selected':['allowed'],'snapshotSignature':'','startDate':'today','endDate':'today','start':now-1,'end':now+100,'sessions':[{'username':'allowed','display':'好友','category':'direct','sortAt':now}],'sessionIndex':0,'sessionCount':1,'offset':0,'messagesProcessed':0,'batchesCompleted':0,'tasksAdded':0,'insights':[]}
   api._analysis_job_worker(d,r,{'dirName':'acct','wxid':'me'},cfg,job)
   saved=analysis_checkpoint.load(d,lambda b:b);self.assertEqual(saved['status'],'failed');self.assertEqual(saved['offset'],21)
   saved['status']='running';api._analysis_job_worker(d,r,{'dirName':'acct','wxid':'me'},cfg,saved)
   complete=analysis_checkpoint.load(d,lambda b:b);self.assertEqual(complete['status'],'complete');self.assertEqual(complete['messagesProcessed'],50);self.assertEqual(calls,['0','21','21','42'])
class ScopeChangeTests(unittest.TestCase):
 def test_scope_change_during_model_call_prevents_commit(self):
  now=int(time.time())
  class R:
   def name_of(self,username):return '我'
   def messages_page(self,username,start,end,offset,limit):return [{'at':now,'text':'请回复','serverId':'1','timeText':'今天'}]
  cfg={'baseUrl':'https://test','model':'m'};r=R()
  with tempfile.TemporaryDirectory() as d, patch.object(api,'READER',r), patch.dict(api.STATE,{'phase':'ready','snapshot':d,'account':{'dirName':'acct','wxid':'me'}},clear=False),patch.object(api.secrets_store,'protect',lambda b:b),patch.object(api.secrets_store,'unprotect',lambda b:b),patch.object(api.agent,'load_config',return_value=cfg):
   scope_policy.save_scope(d,{'allowed'},lambda b:b)
   def model(*args,**kwargs):
    scope_policy.save_scope(d,set(),lambda b:b)
    return {'ok':True,'insights':[{'category':'task','title':'回复','sources':[{'conversationId':'allowed','serverId':'1','at':now,'text':'请回复'}]}]}
   job={'version':1,'id':'j','status':'running','selected':['allowed'],'snapshotSignature':'','startDate':'today','endDate':'today','start':now-1,'end':now+100,'sessions':[{'username':'allowed','display':'好友','category':'direct','sortAt':now}],'sessionIndex':0,'sessionCount':1,'offset':0,'messagesProcessed':0,'batchesCompleted':0,'tasksAdded':0,'insights':[]}
   with patch.object(api.agent,'analyze',side_effect=model):api._analysis_job_worker(d,r,{'dirName':'acct','wxid':'me'},cfg,job)
   self.assertEqual(analysis_checkpoint.load(d,lambda b:b)['status'],'failed')
   self.assertEqual(tasks_store.load(d,lambda b:b),[])

class AdaptiveBatchTests(unittest.TestCase):
 def test_truncation_retries_smaller_batch_without_skipping(self):
  now=int(time.time())
  class R:
   def name_of(self,username):return '我'
   def messages_page(self,username,start,end,offset,limit):return [{'at':now+i,'text':'消息','serverId':str(i),'timeText':'今天'} for i in range(26)][offset:offset+limit]
  cfg={'baseUrl':'https://test','model':'m'};r=R(); sizes=[]
  def model(batch,*args,**kwargs):
   sizes.append(len(batch.messages('allowed')))
   if len(sizes)==1:return {'ok':False,'error':'模型输出达到长度限制'}
   return {'ok':True,'insights':[]}
  with tempfile.TemporaryDirectory() as d,patch.object(api,'READER',r),patch.dict(api.STATE,{'phase':'ready','snapshot':d,'account':{'dirName':'acct','wxid':'me'}},clear=False),patch.object(api.secrets_store,'protect',lambda b:b),patch.object(api.secrets_store,'unprotect',lambda b:b),patch.object(api.agent,'load_config',return_value=cfg),patch.object(api.agent,'analyze',side_effect=model):
   scope_policy.save_scope(d,{'allowed'},lambda b:b)
   job={'version':1,'id':'j','status':'running','selected':['allowed'],'snapshotSignature':'','startDate':'today','endDate':'today','start':now-1,'end':now+100,'sessions':[{'username':'allowed','display':'好友','category':'direct','sortAt':now}],'sessionIndex':0,'sessionCount':1,'offset':0,'messagesProcessed':0,'batchesCompleted':0,'tasksAdded':0,'insights':[]}
   api._analysis_job_worker(d,r,{'dirName':'acct','wxid':'me'},cfg,job)
   self.assertEqual(job['status'],'complete');self.assertEqual(job['messagesProcessed'],26);self.assertEqual(sizes[:2],[24,12])

class PauseTests(unittest.TestCase):
 def test_pause_saves_progress_and_can_resume(self):
  now=int(time.time())
  class R:
   def name_of(self,username):return '我'
   def messages_page(self,username,start,end,offset,limit):return [{'at':now+i,'text':'消息','serverId':str(i),'timeText':'今天'} for i in range(40)][offset:offset+limit]
  cfg={'baseUrl':'https://test','model':'m'};r=R();calls=[]
  def model(batch,*args,**kwargs):
   calls.append(1)
   if len(calls)==1:api.JOB_CANCEL.set()
   return {'ok':True,'insights':[]}
  with tempfile.TemporaryDirectory() as d,patch.object(api,'READER',r),patch.dict(api.STATE,{'phase':'ready','snapshot':d,'account':{'dirName':'acct','wxid':'me'}},clear=False),patch.object(api.secrets_store,'protect',lambda b:b),patch.object(api.secrets_store,'unprotect',lambda b:b),patch.object(api.agent,'load_config',return_value=cfg),patch.object(api.agent,'analyze',side_effect=model):
   scope_policy.save_scope(d,{'allowed'},lambda b:b);api.JOB_CANCEL.clear()
   job={'version':1,'id':'j','status':'running','selected':['allowed'],'snapshotSignature':'','startDate':'today','endDate':'today','start':now-1,'end':now+100,'sessions':[{'username':'allowed','display':'好友','category':'direct','sortAt':now}],'sessionIndex':0,'sessionCount':1,'offset':0,'messagesProcessed':0,'batchesCompleted':0,'tasksAdded':0,'insights':[]}
   api._analysis_job_worker(d,r,{'dirName':'acct','wxid':'me'},cfg,job)
   self.assertEqual(job['status'],'paused');self.assertEqual(job['offset'],21);self.assertEqual(len(calls),1)
   api.JOB_CANCEL.clear();job['status']='running';api._analysis_job_worker(d,r,{'dirName':'acct','wxid':'me'},cfg,job)
   self.assertEqual(job['status'],'complete');self.assertEqual(job['messagesProcessed'],40)

class StartTests(unittest.TestCase):
 def test_job_only_contains_selected_conversations_and_excludes_official(self):
  class R:
   def sessions(self,limit=10000):return [{'username':u,'display':u,'category':c,'sortAt':100} for u,c in [('allowed','direct'),('denied','direct'),('public','official')]]
  class Thread:
   def __init__(self,**kwargs):self.args=kwargs['args']
   def start(self):pass
   def is_alive(self):return False
  cfg={'baseUrl':'https://test','model':'m','apiKey':'secret'};r=R()
  with tempfile.TemporaryDirectory() as d,patch.object(api,'READER',r),patch.object(api,'JOB_THREAD',None),patch.object(api.threading,'Thread',Thread),patch.dict(api.STATE,{'phase':'ready','snapshot':d,'account':{'dirName':'acct','wxid':'me'}},clear=False),patch.object(api.secrets_store,'protect',lambda b:b),patch.object(api.secrets_store,'unprotect',lambda b:b),patch.object(api.agent,'load_config',return_value=cfg):
   scope_policy.save_scope(d,{'allowed','public'},lambda b:b)
   result=api.start_analysis_job({'account':'acct','selected':['allowed','public'],'config':{'baseUrl':'https://test','model':'m'},'startDate':'2026-11-09','endDate':'2026-11-09'})
   self.assertEqual(result['sessionCount'],1)
   saved=analysis_checkpoint.load(d,lambda b:b)
   self.assertEqual([x['username'] for x in saved['sessions']],['allowed'])
   self.assertNotIn('secret',str(saved))

class ModelFormatTests(unittest.TestCase):
 def test_wrappers_and_fenced_json_are_supported(self):
  self.assertEqual(agent._extract_json('说明\n```json\n[{"category":"task"}]\n```'),[{'category':'task'}])
  self.assertEqual(agent._extract_json('{"items":[{"category":"task"}]}'),[{'category':'task'}])
  self.assertIsNone(agent._extract_json('{"reason":"no JSON","evidence":[]}'))
 def test_bad_model_output_retried_without_exposing_raw_text(self):
  payload={'model':'m','messages':[{'role':'user','content':'fake'}]}
  outputs=[{'choices':[{'message':{'content':'not JSON'}}]}, {'choices':[{'message':{'content':'{"items":[]}'}}]}]
  with patch.object(agent,'_post_json',side_effect=outputs) as call:
   parsed,error=agent._request_array(payload,{'baseUrl':'https://test','model':'m'})
  self.assertIsNone(error);self.assertEqual(parsed,[]);self.assertEqual(call.call_count,2)
 def test_bad_output_is_failure_not_empty_result(self):
  payload={'model':'m','messages':[]}
  with patch.object(agent,'_post_json',return_value={'choices':[{'message':{'content':'bad'}}]}):
   parsed,error=agent._request_array(payload,{'baseUrl':'https://test','model':'m'})
  self.assertIsNone(parsed);self.assertIn('有效 JSON',error)

class SnapshotRestoreTests(unittest.TestCase):
 def test_only_reopens_snapshot_when_checkpoint_matches(self):
  import os
  cfg={'baseUrl':'https://test','model':'m'}
  class R:
   def sessions(self,limit):return []
   def close(self):pass
  with tempfile.TemporaryDirectory() as d,patch.object(api,'READER',None),patch.object(api,'JOB_THREAD',None),patch.object(api.secrets_store,'protect',lambda b:b),patch.object(api.secrets_store,'unprotect',lambda b:b),patch.object(api.agent,'load_config',return_value=cfg),patch.object(api.reader,'WxReader',return_value=R()) as open_reader,patch.dict(api.STATE,{'phase':'connecting'},clear=False):
   with open(os.path.join(d,'manifest.json'),'wb') as f:f.write(b'unchanged')
   scope_policy.save_scope(d,{'allowed'},lambda b:b)
   job={'version':1,'status':'failed','selected':['allowed'],'snapshotSignature':api._snapshot_signature(d),'configSignature':api._job_signature(cfg)}
   analysis_checkpoint.save(d,job,lambda b:b)
   self.assertTrue(api._restore_incomplete_snapshot({'dirName':'acct'},d));self.assertEqual(open_reader.call_count,1)
   with open(os.path.join(d,'manifest.json'),'wb') as f:f.write(b'changed')
   self.assertFalse(api._restore_incomplete_snapshot({'dirName':'acct'},d));self.assertEqual(open_reader.call_count,1)

class ListenerTests(unittest.TestCase):
 def test_second_collector_cannot_share_the_listener(self):
  server=api.ExclusiveHTTPServer(('127.0.0.1',0),api.Handler)
  try:
   with self.assertRaises(OSError):api.ExclusiveHTTPServer(('127.0.0.1',server.server_address[1]),api.Handler)
  finally:server.server_close()

class SnapshotRebaseTests(unittest.TestCase):
 def test_changed_snapshot_rechecks_only_current_session(self):
  from datetime import datetime,timedelta
  class R:
   def sessions(self,limit=10000):return []
  class Thread:
   def __init__(self,**kwargs):pass
   def start(self):pass
   def is_alive(self):return False
  cfg={'baseUrl':'https://test','model':'m'};r=R();dte='2026-11-09';start=datetime.strptime(dte,'%Y-%m-%d')
  with tempfile.TemporaryDirectory() as d,patch.object(api,'READER',r),patch.object(api,'JOB_THREAD',None),patch.object(api.threading,'Thread',Thread),patch.dict(api.STATE,{'phase':'ready','snapshot':d,'account':{'dirName':'acct','wxid':'me'}},clear=False),patch.object(api.secrets_store,'protect',lambda b:b),patch.object(api.secrets_store,'unprotect',lambda b:b),patch.object(api.agent,'load_config',return_value=cfg):
   scope_policy.save_scope(d,{'a','b'},lambda b:b)
   old={'version':1,'id':'old','status':'failed','kind':'manual','startDate':dte,'endDate':dte,'start':int(start.timestamp()),'end':int((start+timedelta(days=1)).timestamp()),'selected':['a','b'],'configSignature':api._job_signature(cfg),'snapshotSignature':'previous','sessions':[{'username':'a'},{'username':'b'}],'sessionIndex':1,'sessionCount':2,'offset':21,'messagesProcessed':140,'batchesCompleted':7,'tasksAdded':1,'insights':[{'title':'kept'}]}
   analysis_checkpoint.save(d,old,lambda b:b)
   out=api.start_analysis_job({'account':'acct','selected':['a','b'],'config':cfg,'startDate':dte,'endDate':dte})
   new=analysis_checkpoint.load(d,lambda b:b)
   self.assertEqual(out['id'],'old');self.assertEqual(new['sessionIndex'],1);self.assertEqual(new['offset'],0)
   self.assertEqual(new['messagesProcessed'],116);self.assertEqual(new['insights'],[{'title':'kept'}])
   self.assertIn('重新核查',new['resumeNote'])

if __name__=='__main__':unittest.main()
