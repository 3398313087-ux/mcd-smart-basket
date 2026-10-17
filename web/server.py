#!/usr/bin/env python3
"""Loopback-only UI. Credentials and raw MCP evidence never enter HTTP responses."""
import copy
import json
import os
import secrets
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'skills/mcd-smart-basket/scripts'), str(ROOT/'examples')]
from basket import enumerate_plans, rank_quotes
from demo import snapshot, simulate_quotes
from mcp_readonly import Client
from mcd_adapter import build_price_arguments, normalize_quote

CATALOG=json.loads((Path(__file__).parent/'catalog.json').read_text())
FOODS={f['id']:f['label'] for f in CATALOG}
ALIASES={name:f['id'] for f in CATALOG for name in f['aliases']}
ALIASES['麦乐鸡4块']='nuggets-4'
DEFAULT_DEMAND={'burger':1,'fries-medium':1,'cola-medium':1,'nuggets-5':1}
SESSION = secrets.token_urlsafe(32)
BUSY = threading.Lock()
JOBS = {}

class WebClient(Client):
    """Retry only transient transport failures for this read-only web workflow."""
    def rpc(self, method, params, notification=False):
        for attempt in range(3):
            try:return super().rpc(method,params,notification)
            except RuntimeError as exc:
                transient=any(x in str(exc) for x in ('网络连接失败','HTTP 502','HTTP 503','HTTP 504'))
                if not transient or attempt==2:raise
                time.sleep(attempt+1)

def validate_request(body):
    if not isinstance(body,dict):raise ValueError('请求必须为对象')
    if body.get('source') not in ('demo','live') or body.get('mode') not in ('pickup','delivery','both'):
        raise ValueError('请选择数据来源和用餐方式')
    demand = body.get('demand')
    if not isinstance(demand,dict) or not demand or any(k not in FOODS or type(v) is not int or not 1 <= v <= 2 for k,v in demand.items()):
        raise ValueError('请至少选择一种食品，每种最多两份')
    if type(body.get('extras')) is not bool:
        raise ValueError('请选择是否允许多买')
    return demand

def modifications(detail):
    values=[]
    for g in detail.get('modification',{}).get('items',[]):
        for v in g['values']:
            q=v.get('selectedQuantity',0)
            key=v.get('selectedKey') if q else v.get('unselectedKey')
            if key: values.append({'code':v['code'],'key':key,'quantity':q})
    return {'modification':{'values':values}} if values else {}

def compose_variants(d,demand):
    """Enumerate supported fixed configurations, preserving drink/flavour identity."""
    from itertools import product
    from math import prod
    selection={'productCode':d['code'],**modifications(d)}
    if not d.get('rounds'):
        return [({ALIASES[d['name']]:1},selection,Decimal(0))]
    groups=[]
    for r in d['rounds']:
        choices=[c for c in r['choices'] if ALIASES.get(c['name']) in demand]
        if not choices:choices=[c for c in r['choices'] if c.get('isDefault')==1 and c['name'] in ALIASES]
        q=r.get('quantity') or r['minQuantity']
        choices=[c for c in choices if r['minQuantity']<=q<=r['maxQuantity'] and (c.get('maxQuantity',-1)<0 or q<=c['maxQuantity'])]
        if not choices:raise ValueError('套餐包含尚未支持的餐品或数量')
        groups.append([(r,c,q) for c in choices])
    if prod(map(len,groups))>64:raise ValueError('套餐配置过多，未纳入本轮')
    variants=[]
    # No mixed choices inside a multi-unit round.
    for selected in product(*groups):
        foods=Counter();rounds=[];diff=Decimal(0)
        for r,c,q in selected:
            foods[ALIASES[c['name']]]+=q
            rounds.append({'round':str(r['id']),'comboItemList':[{'code':c['code'],'quantity':q,**modifications(c)}]})
            m=re.search(r'([+-])\s*¥\s*(\d+(?:\.\d+)?)',c.get('diffPrice',''))
            if m:diff+=Decimal(m[2])*(1 if m[1]=='+' else -1)*q
        variants.append((dict(foods),dict(selection,roundList=rounds),diff))
    return variants

def compose_detail(d,demand):
    return compose_variants(d,demand)[0]


def live_snapshot(body,progress):
    mode=body['mode']; private=ROOT/'private'
    config=private/f'{mode}-snapshot.json'
    if not config.exists():raise ValueError('尚未配置真实门店，请让助手按 live-workflow.md 准备本机快照')
    data=json.loads(config.read_text()); data['demand']=body['demand']; data['max_extra_units']=1 if body['extras'] else 0
    token=os.environ.get('MCD_MCP_TOKEN') or (private/'mcd-token').read_text().strip()
    client=WebClient(token); client.initialize()
    context=data['context']; context['service_time']='immediate'
    args={'storeCode':context['store_id'],'orderType':1 if mode=='pickup' else 2,'beType':1 if mode=='pickup' else 2}
    if mode=='delivery':
        progress('确认已有地址和配送门店')
        old=json.loads((private/'addresses-response.json').read_text())['structuredContent']['data']['addresses']
        current=client.call('delivery-query-addresses',{})['structuredContent']['data']['addresses']
        old_id=old[0]['addressId'] if old else None
        if not old_id or not any(a['addressId']==old_id for a in current):raise ValueError('已绑定地址发生变化，请重新选择地址')
        stores=client.call('delivery-query-stores',{'addressId':old_id,'beType':2})['structuredContent']['data']
        if not stores:raise ValueError('该地址当前没有可用配送门店')
        store=next((s for s in stores if s['storeCode']==context['store_id']),stores[0])
        context.update(store_id=store['storeCode'],store_name=store['storeName'],be_code=store['beCode'])
        args.update(storeCode=context['store_id'],beCode=context['be_code'])
    progress('获取最新菜单和已有优惠券')
    menu=client.call('query-meals',args)['structuredContent']
    coupons=client.call('query-store-coupons',args)['structuredContent']['data']
    cmap={p['productCode']:(i,c) for i,c in enumerate(coupons) for p in c.get('products',[])}
    selected=[f for f in CATALOG if f['id'] in body['demand']]
    # Singles first: a large catalog must never crowd out the baseline.
    codes=list(dict.fromkeys(f['codes'][mode] for f in selected))
    patterns=[word for f in selected for word in f['match']]
    related=[code for code,m in menu['data']['meals'].items() if any(word in m['name'] for word in patterns)]
    old_codes=[o['official_selection']['productCode'] for o in data['offers']]
    all_codes=list(dict.fromkeys(codes+related+list(cmap)+old_codes))
    codes=all_codes[:24];offers=[];skipped=max(0,len(all_codes)-len(codes))
    for index,code in enumerate(codes):
        progress(f'核对相关餐品 {index+1}/{len(codes)}')
        coupon=cmap.get(code)
        if not coupon and code not in menu['data']['meals']:skipped+=1;continue
        detail=client.call('query-meal-detail',dict(args,code=code))['structuredContent']['data']
        try:variants=compose_variants(detail,body['demand'])
        except (KeyError,ValueError):skipped+=1;continue
        for variant,(foods,selection,diff) in enumerate(variants):
            # Ignore configurations that cannot fit the user's extra-food allowance.
            if sum(max(0,n-body['demand'].get(k,0)) for k,n in foods.items())>data['max_extra_units']:continue
            if not any(k in body['demand'] for k in foods):continue
            if len(offers)>=80:skipped+=1;continue
            name=FOODS.get(next(iter(foods)),detail['name']) if sum(foods.values())==1 else detail['name']
            row={'id':code+'-'+str(variant),'name':name,'items':foods,
                 'price_cents':max(0,int((Decimal(menu['data']['meals'].get(code,{}).get('currentPrice','0'))+diff)*100)),
                 'max_quantity':2,'official_selection':selection}
            if coupon:
                i,c=coupon;row['coupon_usage']={f'coupon-{i}':1};row['max_quantity']=1
                row['name']='已有券：'+row['name'];selection.update(couponId=c['couponId'],couponCode=c['couponCode'])
            offers.append(row)
    data['labels'].update(FOODS)
    data.update(offers=offers,coupon_limits={f'coupon-{i}':1 for i,_ in enumerate(coupons)},captured_at=datetime.now(timezone.utc).isoformat())
    return data,client,skipped

def public_result(data,result):
    offers={o['id']:o for o in data['offers']}
    def item(c):
        o=offers[c['offer_id']]
        return {'name':o['name'],'quantity':c['quantity'],
                'contents':[{'name':FOODS.get(k,data['labels'].get(k,k)),'quantity':v*c['quantity']} for k,v in o['items'].items()],
                'uses_coupon':bool(o.get('coupon_usage'))}
    def plan(p):
        q=p['quote']; total=q['total_cents']; baseline=result['baseline_cents']
        return {'items':[item(c) for c in p['cart']],
                'extras':[{'name':data['labels'].get(k,k),'quantity':v} for k,v in p['extras'].items()],
                'total':total,'delivery':q['delivery_fee_cents'],'packing':q['packaging_fee_cents'],
                'food_total':total-q['delivery_fee_cents']-q['packaging_fee_cents'],
                'saving':None if baseline is None else baseline-total,'quoted_at':q['quoted_at']}
    exact=result['exact']; best=exact[0]['quote']['total_cents'] if exact else None
    extras=[p for p in result['extra'] if best is None or p['quote']['total_cents']<best]
    return {'source':data['source'],'mode':data['context']['mode'],'store':data['context'].get('store_name','虚构演示门店'),
            'exact':[plan(p) for p in exact[:3]],'extras':[plan(p) for p in extras[:2]],'baseline':result['baseline_cents'],
            'candidates':result['candidate_count'],'priced':result['priced_count'],'unquoted':result['unquoted_count'],
            'invalid':len(result['invalid']),'complete':result['search_complete'],'captured_at':data['captured_at']}

def demo_snapshot(body):
    data=snapshot(body['mode']);data['demand']=body['demand'];data['max_extra_units']=int(body['extras'])
    data['labels'].update(FOODS)
    for f in CATALOG:
        if f['id'] in DEFAULT_DEMAND:continue
        data['offers'].append({'id':'demo-'+f['id'],'name':f['label']+'单点','items':{f['id']:1},'price_cents':f['demo_price'],'max_quantity':2})
        if f['category']=='burger':
            for drink in ['cola-medium','zero-medium']:
                data['offers'].append({'id':'demo-meal-'+f['id']+'-'+drink,'name':f['name']+'中套餐','items':{f['id']:1,'fries-medium':1,drink:1},'price_cents':f['demo_price']+900,'max_quantity':2})
    data['offers']=[o for o in data['offers'] if any(k in body['demand'] for k in o['items']) and sum(max(0,n-body['demand'].get(k,0)) for k,n in o['items'].items())<=data['max_extra_units']]
    return data

def compare(body,progress=lambda _:None):
    validate_request(body)
    if body['mode']=='both':
        return compare_both(body,progress)
    if body['source']=='demo':
        data=demo_snapshot(body)
        search=enumerate_plans(data);quotes=simulate_quotes(data,search);skipped=0
    else:
        data,client,skipped=live_snapshot(body,progress)
        search=enumerate_plans(data)
        plans=sorted(search['plans'],key=lambda p:(not p['single_item_baseline'],bool(p['extras']),p['estimate_cents']))
        exact=[p for p in plans if not p['extras']];extra=[p for p in plans if p['extras']]
        chosen=(exact[:8]+extra[:2])[:10] if body['extras'] else exact[:10]
        quotes={'source':'live','snapshot_id':search['snapshot_id'],'quotes':[]}
        folder=ROOT/'private'/'web-evidence'/secrets.token_hex(8);folder.mkdir(mode=0o700,parents=True)
        for i,p in enumerate(chosen):
            progress(f'官方整单计价 {i+1}/{len(chosen)}')
            request=build_price_arguments(data,p)
            response=client.call('calculate-price',request)
            evidence=folder/f'{i}.json'
            evidence.write_text(json.dumps({'request':request,'response':response},ensure_ascii=False));evidence.chmod(0o600)
            quotes['quotes'].append(normalize_quote(p['id'],request,response,str(evidence)))
    result=public_result(data,rank_quotes(data,search,quotes));result['skipped']=skipped
    return result

def compare_both(body,progress):
    # Each channel has its own menu, coupons, fees and quote context.
    results={};errors={}
    for mode,label in [('pickup','到店取餐'),('delivery','麦乐送')]:
        try:
            results[mode]=compare(dict(body,mode=mode),lambda msg:progress(label+' · '+msg))
        except Exception:
            # Do not disclose upstream responses, addresses or credentials.
            errors[mode]='本轮查询未完成，请单独重试'
    return {'source':body['source'],'mode':'both','results':results,'errors':errors}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def reply(self,status,body,kind='application/json'):
        raw=json.dumps(body,ensure_ascii=False).encode() if kind=='application/json' else body
        self.send_response(status);self.send_header('Content-Type',kind+'; charset=utf-8');self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data: https://menu-img.mcd.cn; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers();self.wfile.write(raw)
    def valid_host(self):return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
    def do_GET(self):
        if not self.valid_host():return self.reply(403,{'error':'仅允许本机访问'})
        if self.path=='/api/catalog':return self.reply(200,[{k:f[k] for k in ('id','name','spec','label','category','image')} for f in CATALOG])
        if self.path=='/api/status':return self.reply(200,{'session':SESSION,'live_ready':all((ROOT/'private'/n).is_file() for n in ['mcd-token','pickup-snapshot.json','delivery-snapshot.json']),'store':'北京海淀镇餐厅'})
        if self.path.startswith('/api/jobs/'):
            if self.headers.get('X-Session')!=SESSION:return self.reply(403,{'error':'请刷新页面'})
            return self.reply(200,JOBS.get(self.path.rsplit('/',1)[-1],{'status':'error','error':'任务不存在'}))
        files={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
        if self.path not in files:return self.reply(404,{'error':'页面不存在'})
        name=files[self.path];kind={'html':'text/html','js':'text/javascript','css':'text/css'}[name.rsplit('.',1)[-1]]
        return self.reply(200,(Path(__file__).parent/name).read_bytes(),kind)
    def do_POST(self):
        if not self.valid_host() or self.headers.get('X-Session')!=SESSION or self.headers.get('Origin') not in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'):
            return self.reply(403,{'error':'请从本机网页发起查询'})
        if self.path!='/api/compare':return self.reply(404,{'error':'接口不存在'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=4096:raise ValueError('请求过大')
            body=json.loads(self.rfile.read(length));validate_request(body)
        except (ValueError,TypeError):return self.reply(400,{'error':'请选择至少一种食品，每种最多两份'})
        if not BUSY.acquire(False):return self.reply(409,{'error':'上一轮比较还在进行，请稍候'})
        # Keep only a small bounded number of completed, sanitized results.
        while len(JOBS)>=10:JOBS.pop(next(iter(JOBS)))
        jid=secrets.token_hex(16);JOBS[jid]={'status':'running','message':'正在准备比较'}
        def run():
            try:JOBS[jid]={'status':'done','result':compare(body,lambda msg:JOBS[jid].update(message=msg))}
            except Exception as exc:
                import traceback
                frames=[f'{Path(f.filename).name}:{f.lineno}' for f in traceback.extract_tb(exc.__traceback__)]
                print('Query failed:',type(exc).__name__,','.join(frames),flush=True)
                JOBS[jid]={'status':'error','error':'本轮查询未完成。请检查本机连接、门店营业时间或稍后重试；没有使用旧报价代替。'}
            finally:BUSY.release()
        threading.Thread(target=run,daemon=True).start();self.reply(202,{'job':jid})

if __name__=='__main__':
    port=int(os.environ.get('MCD_WEB_PORT','8765'))
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print(f'麦麦省心凑单：http://127.0.0.1:{port}',flush=True)
    server.serve_forever()
