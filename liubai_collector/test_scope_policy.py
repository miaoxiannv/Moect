import tempfile, time, unittest
import agent, insights, scope_policy
class FakeReader:
 def __init__(self): self.reads=[]
 def name_of(self,username): return username
 def sessions(self,limit=200): return [{"username":u,"display":u,"category":"direct","sortAt":time.time(),"unread":1} for u in ("blocked","allowed")][:limit]
 def messages(self,username,limit=60):
  self.reads.append(username)
  return [{"text":"请帮忙合作？","senderId":"other","sender":"Alice","at":time.time(),"serverId":"1","timeText":"today"}]
class ScopeTests(unittest.TestCase):
 def test_roundtrip_and_fail_closed(self):
  with tempfile.TemporaryDirectory() as d:
   self.assertEqual(scope_policy.load_scope(d,lambda x:x),set())
   seal=lambda data:b"sealed:"+data
   unseal=lambda data:data.removeprefix(b"sealed:") if data.startswith(b"sealed:") else None
   scope_policy.save_scope(d,{"allowed"},seal)
   self.assertEqual(scope_policy.load_scope(d,unseal),{"allowed"})
   self.assertEqual(scope_policy.load_scope(d,lambda _:None),set())
 def test_both_analysis_paths_exclude_denied(self):
  r=FakeReader(); s=scope_policy.ScopedReader(r,{"allowed"})
  corpus,index,_=agent.build_corpus(s,"self",3)
  self.assertTrue(index);self.assertIn("allowed",corpus);self.assertNotIn("blocked",corpus)
  insights.build_digest(s,"self","",recent_days=3)
  self.assertEqual(r.reads,["allowed","allowed"])
  with self.assertRaises(PermissionError):s.messages("blocked")
 def test_empty_scope_reads_nothing(self):
  r=FakeReader();s=scope_policy.ScopedReader(r,set())
  agent.build_corpus(s,"self",3);insights.build_digest(s,"self","",recent_days=3)
  self.assertEqual(r.reads,[])
if __name__=="__main__":unittest.main()

class APIScopeTests(unittest.TestCase):
 def test_account_mismatch_and_permission(self):
  import api
  from unittest.mock import patch
  r=FakeReader()
  with tempfile.TemporaryDirectory() as d, patch.object(api,"READER",r), patch.dict(api.STATE,{"phase":"ready","snapshot":d,"account":{"dirName":"account-a","wxid":"self"}},clear=False), patch.object(api.secrets_store,"protect",lambda b:b), patch.object(api.secrets_store,"unprotect",lambda b:b):
   with self.assertRaises(ValueError):api.save_scope({"account":"account-b","selected":["allowed"]})
   with self.assertRaises(ValueError):api.save_scope({"account":"account-a","selected":["unknown"]})
   with self.assertRaises(PermissionError):api.handle_messages("allowed",10)
   api.save_scope({"account":"account-a","selected":["allowed"]})
   self.assertTrue(api.handle_messages("allowed",10)["ready"])
   with self.assertRaises(PermissionError):api.handle_messages("blocked",10)

class AnalysisBindingTests(unittest.TestCase):
 def test_changed_scope_or_model_never_calls_agent(self):
  import api
  from unittest.mock import patch
  r=FakeReader()
  with tempfile.TemporaryDirectory() as d, patch.object(api,"READER",r), patch.dict(api.STATE,{"phase":"ready","snapshot":d,"account":{"dirName":"account-a","wxid":"self"}},clear=False), patch.object(api.secrets_store,"protect",lambda b:b), patch.object(api.secrets_store,"unprotect",lambda b:b), patch.object(api.agent,"load_config",return_value={"baseUrl":"https://a.test","model":"m"}), patch.object(api.agent,"analyze") as analyze:
   api.save_scope({"account":"account-a","selected":["allowed"]})
   with self.assertRaises(ValueError):api.handle_analyze(5,"account-a",["blocked"],{"baseUrl":"https://a.test","model":"m"})
   with self.assertRaises(ValueError):api.handle_analyze(5,"account-a",["allowed"],{"baseUrl":"https://other.test","model":"m"})
   analyze.assert_not_called()


class AnalysisTaskPersistenceTests(unittest.TestCase):
 def test_only_successful_analysis_saves_evidence_backed_tasks(self):
  import api
  from unittest.mock import patch
  r=FakeReader()
  cfg={'baseUrl':'https://test','model':'test'}
  result={'ok':True,'insights':[{'category':'task','title':'回复方案','sources':[{'conversationId':'allowed','conversationName':'群','serverId':'1','timeText':'2026-01-01','text':'请你回复方案'}]}]}
  with tempfile.TemporaryDirectory() as d, patch.object(api,'READER',r), patch.dict(api.STATE,{'phase':'ready','snapshot':d,'account':{'dirName':'account-a','wxid':'self'}},clear=False), patch.object(api.secrets_store,'protect',lambda b:b), patch.object(api.secrets_store,'unprotect',lambda b:b), patch.object(api.agent,'load_config',return_value=cfg), patch.object(api.agent,'analyze',return_value=result):
   api.save_scope({'account':'account-a','selected':['allowed']})
   response=api.handle_analyze(5,'account-a',['allowed'],cfg)
   self.assertEqual(response['tasksAdded'],1)
   saved=api.tasks_store.load(d,lambda b:b)
   self.assertEqual(saved[0]['sourceText'],'请你回复方案')
