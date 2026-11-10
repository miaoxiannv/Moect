import unittest
from unittest.mock import patch
import key

class KeyExtractionTests(unittest.TestCase):
 def test_silent_scan_enables_privilege(self):
  def scan(pid,index,found,verbose=False,passphrase_budget=None):
   found['salt']='ab'*32
   return {'pid':pid,'readable_mb':1,'needle':1,'refs':1,'nodes':1,'blobs':1,'candidates':1,'hit':1}
  with patch.object(key,'collect_salts',return_value={'salt':b'page'}), patch.object(key.winmem,'weixin_processes',return_value=[{'pid':7,'mem_kb':1}]), patch.object(key.winmem,'enable_debug_privilege',return_value=True) as enable, patch.object(key,'scan_process',side_effect=scan):
   diagnostics={}
   result=key.extract('unused',verbose=False,diagnostics=diagnostics)
   enable.assert_called_once();self.assertEqual(len(result),1);self.assertEqual(diagnostics['processesOpened'],1)
 def test_legacy_fallback_when_modern_scan_finds_nothing(self):
  def legacy(pid,index,found):found['salt']='cd'*32;return 1
  empty={'pid':7,'readable_mb':1,'needle':0,'refs':0,'nodes':0,'blobs':0,'candidates':0,'hit':0}
  with patch.object(key,'collect_salts',return_value={'salt':b'page'}), patch.object(key.winmem,'weixin_processes',return_value=[{'pid':7,'mem_kb':1}]), patch.object(key.winmem,'enable_debug_privilege',return_value=False), patch.object(key,'scan_process',return_value=empty), patch.object(key,'scan_legacy',side_effect=legacy) as fallback:
   result=key.extract('unused',verbose=False);self.assertEqual(len(result),1);fallback.assert_called_once()

class CachedKeyValidationTests(unittest.TestCase):
 def test_database_key_is_validated_against_page(self):
  import api, cipher, os, tempfile
  rawkey=bytes(range(32)); page=bytearray(4096); page[:16]=bytes(range(16));page[-64:]=cipher.hmac_for_page1(rawkey,bytes(page))
  with tempfile.TemporaryDirectory() as d:
   os.makedirs(os.path.join(d,'session'))
   with open(os.path.join(d,'session','session.db'),'wb') as f:f.write(page)
   salt=page[:16].hex()
   self.assertEqual(api._missing_core_keys(d,{salt:rawkey.hex()}),[])
   self.assertEqual(api._missing_core_keys(d, {salt: '01' * 32}), ['session/session.db'])

class PassphraseFallbackTests(unittest.TestCase):
 def test_passphrase_fallback_only_after_no_direct_key(self):
  calls=[]
  def scan(pid,index,found,verbose=False,passphrase_budget=None):
   calls.append(passphrase_budget is not None)
   if passphrase_budget is not None:found['salt']='ef'*32
   return {'pid':pid,'readable_mb':1,'needle':1,'refs':1,'nodes':1,'blobs':1,'candidates':1,'hit':int(bool(found))}
  with patch.object(key,'collect_salts',return_value={'salt':b'page'}), patch.object(key.winmem,'weixin_processes',return_value=[{'pid':7,'mem_kb':1}]), patch.object(key.winmem,'enable_debug_privilege',return_value=False), patch.object(key,'scan_process',side_effect=scan), patch.object(key,'scan_legacy',return_value=0):
   self.assertEqual(len(key.extract('unused',verbose=False)),1)
   self.assertEqual(calls,[False,True])
