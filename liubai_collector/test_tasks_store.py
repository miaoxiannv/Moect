import os
import base64
import tempfile
import unittest
import tasks_store

class TasksTests(unittest.TestCase):
    def test_add_toggle_delete_and_reload(self):
        with tempfile.TemporaryDirectory() as d:
            protect = lambda b: base64.b64encode(b)
            unprotect = lambda b: base64.b64decode(b)
            items = tasks_store.update(d, {'op':'add','title':'回复方案','source':'群聊'}, protect, unprotect)
            with open(os.path.join(d,'tasks.bin'),'rb') as f:
                self.assertNotIn('回复方案'.encode(), f.read())
            item_id = items[0]['id']
            self.assertTrue(tasks_store.update(d, {'op':'toggle','id':item_id}, protect, unprotect)[0]['done'])
            self.assertEqual(tasks_store.load(d,unprotect)[0]['source'],'群聊')
            self.assertEqual(tasks_store.update(d, {'op':'delete','id':item_id}, protect, unprotect), [])

    def test_corrupt_data_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,'tasks.bin')
            with open(path,'wb') as f: f.write(b'bad')
            with self.assertRaises(ValueError): tasks_store.update(d, {'op':'add','title':'test'}, lambda b:b, lambda b:None)
            with open(path,'rb') as f: self.assertEqual(f.read(), b'bad')

    def test_model_merge_is_deduplicated_and_preserves_dismissal(self):
        with tempfile.TemporaryDirectory() as d:
            item = {'category':'task','title':'回复方案','sources':[{'conversationId':'chat','serverId':'1','conversationName':'群聊','timeText':'2026-11-09','text':'请你回复方案'}]}
            self.assertEqual(tasks_store.merge_analysis(d,[item],lambda b:b,lambda b:b),1)
            saved=tasks_store.load(d,lambda b:b)
            self.assertEqual(saved[0]['sourceText'],'请你回复方案')
            tasks_store.update(d,{'op':'toggle','id':saved[0]['id']},lambda b:b,lambda b:b)
            self.assertEqual(tasks_store.merge_analysis(d,[item],lambda b:b,lambda b:b),0)
            self.assertTrue(tasks_store.load(d,lambda b:b)[0]['done'])
            tasks_store.update(d,{'op':'delete','id':saved[0]['id']},lambda b:b,lambda b:b)
            self.assertEqual(tasks_store.merge_analysis(d,[item],lambda b:b,lambda b:b),0)
            self.assertEqual(tasks_store.visible(tasks_store.load(d,lambda b:b)),[])

    def test_unbacked_or_non_task_never_becomes_task(self):
        with tempfile.TemporaryDirectory() as d:
            items=[{'category':'task','title':'test','sources':[]},{'category':'group','title':'test','sources':[{'conversationId':'x'}]}]
            self.assertEqual(tasks_store.merge_analysis(d,items,lambda b:b,lambda b:b),0)

    def test_completion_is_only_a_suggestion_from_same_conversation(self):
        with tempfile.TemporaryDirectory() as d:
            evidence={'conversationId':'one','serverId':'a','at':100,'text':'我明天发你','timeText':'上午'}
            tasks_store.merge_analysis(d,[{'category':'task','title':'发送','assignee':'self','sources':[evidence]}],lambda b:b,lambda b:b)
            original=tasks_store.load(d,lambda b:b)[0]
            update={'category':'task_update','taskId':original['id'],'sources':[{'conversationId':'other','serverId':'b','at':200,'text':'发好了','timeText':'下午'}]}
            tasks_store.merge_analysis(d,[update],lambda b:b,lambda b:b)
            self.assertNotIn('completionSuggestion',tasks_store.load(d,lambda b:b)[0])
            update['sources'][0]['conversationId']='one'
            tasks_store.merge_analysis(d,[update],lambda b:b,lambda b:b)
            task=tasks_store.load(d,lambda b:b)[0]
            self.assertFalse(task['done'])
            self.assertEqual(task['completionSuggestion']['text'],'发好了')

    def test_one_message_can_contain_two_actions(self):
        with tempfile.TemporaryDirectory() as d:
            ref={'conversationId':'chat','serverId':'1','at':100,'text':'我发送资料并预约会议'}
            items=[{'category':'task','title':'发送资料','sources':[ref]},{'category':'task','title':'预约会议','sources':[ref]}]
            self.assertEqual(tasks_store.merge_analysis(d,items,lambda b:b,lambda b:b),2)
            self.assertEqual(tasks_store.merge_analysis(d,items,lambda b:b,lambda b:b),0)

if __name__ == '__main__': unittest.main()
