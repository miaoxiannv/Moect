import unittest
from unittest.mock import patch
from core import liubai_bridge as bridge

class Service:
    def __init__(self,wxid='account',demo=False): self.library={'live':True,'demo':demo,'profile':{'wxid':wxid}}
    def state(self): return {'library':self.library}

class BridgeTests(unittest.TestCase):
    def test_rejects_mismatched_account_before_scope_save(self):
        def response(path, body=None, timeout=10):
            self.assertEqual(path,'/api/status')
            return {'state':{'phase':'ready','account':{'wxid':'other','dirName':'other'}}}
        with patch.object(bridge,'request',side_effect=response):
            with self.assertRaises(ValueError): bridge.invoke(Service(),{'operation':'save_scope','selected':['person']})
    def test_saves_only_ids_with_matching_account(self):
        calls=[]
        def response(path, body=None, timeout=10):
            calls.append((path,body))
            if path=='/api/status': return {'state':{'phase':'ready','account':{'wxid':'account','dirName':'account-dir'}}}
            return {'ok':True}
        with patch.object(bridge,'request',side_effect=response):
            bridge.invoke(Service(),{'operation':'save_scope','selected':['person']})
        self.assertEqual(calls[-1],('/api/scope',{'account':'account-dir','selected':['person']}))
    def test_demo_data_cannot_be_sent(self):
        with patch.object(bridge,'request',return_value={'state':{'phase':'ready','account':{'wxid':'account'}}}):
            with self.assertRaises(ValueError): bridge.invoke(Service(demo=True),{'operation':'analyze'})

if __name__=='__main__': unittest.main()
