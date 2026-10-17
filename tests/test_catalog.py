import unittest
from test_web import web
from collections import Counter

class CatalogTests(unittest.TestCase):
    def test_every_catalog_food_can_be_bought_in_demo(self):
        for food in web.CATALOG:
            for mode in ['pickup','delivery']:
                with self.subTest(food=food['id'],mode=mode):
                    r=web.compare(dict(source='demo',mode=mode,demand={food['id']:1},extras=False))
                    self.assertTrue(r['exact'])
                    contents=Counter()
                    for item in r['exact'][0]['items']:
                        for c in item['contents']:contents[c['name']]+=c['quantity']
                    self.assertEqual(contents,{food['label']:1})
    def test_same_parent_keeps_both_pie_flavours(self):
        detail={'code':'pie','rounds':[{'id':1,'minQuantity':1,'maxQuantity':1,'quantity':1,'choices':[
            {'code':'a','name':'菠萝派','isDefault':1,'maxQuantity':-1},
            {'code':'b','name':'香芋派','maxQuantity':-1}]}]}
        variants=web.compose_variants(detail,{'pineapple-pie':1,'taro-pie':1})
        self.assertEqual([v[0] for v in variants],[{'pineapple-pie':1},{'taro-pie':1}])
        self.assertEqual([v[1]['roundList'][0]['comboItemList'][0]['code'] for v in variants],['a','b'])
    def test_sugar_free_cannot_be_replaced_by_regular(self):
        detail={'code':'drink','rounds':[{'id':1,'minQuantity':1,'maxQuantity':1,'quantity':1,'choices':[
            {'code':'regular','name':'可乐中杯','isDefault':1,'maxQuantity':-1},
            {'code':'zero','name':'无糖可口可乐中杯','maxQuantity':-1}]}]}
        v=web.compose_variants(detail,{'zero-medium':1})
        self.assertEqual(len(v),1);self.assertEqual(v[0][0],{'zero-medium':1})
    def test_new_burger_meal_and_sweet_do_not_change_request(self):
        demand={'filet':1,'fries-medium':1,'zero-medium':1,'taro-pie':1}
        result=web.compare(dict(source='demo',mode='both',demand=demand,extras=True))
        for r in result['results'].values():
            contents=Counter()
            for item in r['exact'][0]['items']:
                for c in item['contents']:contents[c['name']]+=c['quantity']
            self.assertEqual(contents,{web.FOODS[k]:v for k,v in demand.items()})
    def test_delivery_uses_fresh_store_context_for_menu_and_details(self):
        import json,tempfile
        from pathlib import Path
        from unittest.mock import patch
        for code in ['old-store','new-store']:
            with self.subTest(store=code),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);private=root/'private';private.mkdir()
                (private/'delivery-snapshot.json').write_text(json.dumps({'context':{'store_id':'old-store','be_code':'old-business','mode':'delivery'},'offers':[],'labels':{}}))
                (private/'addresses-response.json').write_text(json.dumps({'structuredContent':{'data':{'addresses':[{'addressId':'authorized-address'}]}}}))
                (private/'mcd-token').write_text('test-only')
                calls=[]
                class FakeClient:
                    def __init__(self,token):pass
                    def initialize(self):pass
                    def call(self,name,args):
                        calls.append((name,args))
                        data={'delivery-query-addresses':{'addresses':[{'addressId':'authorized-address'}]},
                              'delivery-query-stores':[{'storeCode':code,'storeName':'当前配送店','beCode':'fresh-business'}],
                              'query-meals':{'meals':{'901600':{'name':'麦香鱼','currentPrice':'24'}}},
                              'query-store-coupons':[],
                              'query-meal-detail':{'code':'901600','name':'麦香鱼','rounds':[]}}[name]
                        return {'structuredContent':{'success':True,'data':data}}
                with patch.object(web,'ROOT',root),patch.object(web,'WebClient',FakeClient):
                    data,_,_=web.live_snapshot(dict(mode='delivery',demand={'filet':1},extras=False),lambda _:None)
                self.assertEqual(data['context']['store_id'],code)
                self.assertEqual(data['context']['be_code'],'fresh-business')
                for name,args in calls:
                    if name in ['query-meals','query-store-coupons','query-meal-detail']:
                        self.assertEqual(args['storeCode'],code);self.assertEqual(args['beCode'],'fresh-business')

    def test_catalog_metadata_is_unique_and_official_images_only(self):
        self.assertEqual(len(web.CATALOG),len(web.FOODS))
        aliases=[a for f in web.CATALOG for a in f['aliases']]
        self.assertEqual(len(aliases),len(set(aliases)))
        for f in web.CATALOG:
            self.assertTrue(f['image'].startswith('https://menu-img.mcd.cn/'))
            self.assertEqual(set(f['codes']),{'pickup','delivery'})

if __name__=='__main__':unittest.main()
