import importlib.util
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('basket_web',ROOT/'web/server.py')
web=importlib.util.module_from_spec(spec);spec.loader.exec_module(web)
class WebTests(unittest.TestCase):
    def body(self,**kwargs):return dict(source='demo',mode='delivery',demand={k:1 for k in web.FOODS},extras=True,**kwargs)
    def test_demo_fee_and_extra_savings(self):
        r=web.compare(self.body());self.assertEqual(r['exact'][0]['total'],4700);self.assertEqual(r['extras'][0]['total'],4600)
        self.assertEqual(r['exact'][0]['packing'],100)
    def test_exact_only(self):
        b=self.body();b['extras']=False;r=web.compare(b);self.assertFalse(r['extras'])
    def test_changed_quantities_recompute(self):
        b=self.body();b['demand']={'nuggets-5':2};r=web.compare(b)
        self.assertEqual(r['exact'][0]['items'],[{'name':'5块鸡块单点','quantity':2}])
    def test_empty_and_malformed_demand(self):
        for d in [{},{'burger':True},{'burger':3},{'unknown':1},{'burger':-1}]:
            b=self.body();b['demand']=d
            with self.assertRaises(ValueError):web.compare(b)
    def test_public_response_excludes_account_and_request_data(self):
        r=web.compare(self.body());import json
        s=json.dumps(r)
        for key in ['official_selection','couponCode','couponId','evidence_ref','address_key','be_code','session']:self.assertNotIn(key,s)
    def test_unmapped_combo_is_not_guessed(self):
        detail={'code':'x','rounds':[{'id':1,'minQuantity':1,'maxQuantity':1,'choices':[{'name':'未知食品','code':'y','isDefault':1}]}]}
        with self.assertRaises(ValueError):web.compose_detail(detail,{'burger':1})
    def test_compose_uses_fresh_detail(self):
        detail={'code':'x','rounds':[{'id':1,'minQuantity':1,'maxQuantity':1,'choices':[{'name':'麦辣鸡腿汉堡','code':'fresh','diffPrice':'+¥2','maxQuantity':-1}]}]}
        foods,sel,price=web.compose_detail(detail,{'burger':1})
        self.assertEqual(foods,{'burger':1});self.assertEqual(sel['roundList'][0]['comboItemList'][0]['code'],'fresh');self.assertEqual(price,2)
    def test_transient_read_failure_retries(self):
        from unittest.mock import patch
        c=web.WebClient('test-placeholder')
        with patch.object(web.Client,'rpc',side_effect=[RuntimeError('网络连接失败'),{'ok':True}]) as rpc, patch.object(web.time,'sleep'):
            self.assertEqual(c.rpc('tools/list',{}),{'ok':True});self.assertEqual(rpc.call_count,2)
    def test_auth_failure_never_retries(self):
        from unittest.mock import patch
        c=web.WebClient('test-placeholder')
        with patch.object(web.Client,'rpc',side_effect=RuntimeError('HTTP 401')) as rpc:
            with self.assertRaises(RuntimeError):c.rpc('tools/list',{})
            self.assertEqual(rpc.call_count,1)
if __name__=='__main__':unittest.main()

