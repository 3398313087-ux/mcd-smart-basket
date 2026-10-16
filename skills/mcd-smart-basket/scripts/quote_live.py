#!/usr/bin/env python3
"""Price a bounded set of prepared candidates using only calculate-price."""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys
from basket import digest, read, rank_quotes, render
from mcd_adapter import build_price_arguments, normalize_quote
from mcp_readonly import Client


def main():
    parser = argparse.ArgumentParser(description='真实候选整单计价；只调用 calculate-price，不下单')
    parser.add_argument('snapshot')
    parser.add_argument('search')
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--out', default='private/live-quotes')
    args = parser.parse_args()
    if not 1 <= args.limit <= 20:
        parser.error('每次核价数量须为 1–20')
    out = Path(args.out).resolve()
    private = Path('private').resolve()
    if out != private and private not in out.parents:
        parser.error('真实请求和响应只能保存在当前项目 private/ 下')
    data, search = read(args.snapshot), read(args.search)
    if data['source'] != 'live' or search['snapshot_id'] != digest(data):
        parser.error('需要本次真实快照及其对应候选')
    rank_quotes(data, search, {'source': 'live', 'snapshot_id': search['snapshot_id'], 'quotes': []})
    plans = sorted(search['plans'], key=lambda p: (not p['single_item_baseline'], bool(p['extras']), p['estimate_cents']))[:args.limit]
    jobs = [(plan, build_price_arguments(data, plan)) for plan in plans]
    token = os.environ.get('MCD_MCP_TOKEN')
    if not token and Path('private/mcd-token').is_file():
        token = Path('private/mcd-token').read_text().strip()
    if not token and sys.stdin.isatty():
        token = getpass.getpass('麦当劳 Token（隐藏输入）：')
    if not token:
        parser.error('请先完成本机连接设置')
    client = Client(token)
    client.initialize()
    if 'calculate-price' not in {t['name'] for t in client.list_tools()}:
        parser.error('当前服务未提供价格计算')
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    quotes = {'source': 'live', 'snapshot_id': search['snapshot_id'], 'quotes': []}
    for index, (plan, request) in enumerate(jobs):
        ref = out / f'response-{index}.json'
        (out / f'request-{index}.json').write_text(json.dumps(request, ensure_ascii=False, indent=2))
        try:
            response = client.rpc('tools/call', {'name': 'calculate-price', 'arguments': request})
            ref.write_text(json.dumps(response, ensure_ascii=False, indent=2))
            quote = normalize_quote(plan['id'], request, response, str(ref))
        except (RuntimeError, ValueError, KeyError, TypeError, OSError):
            quote = {'plan_id': plan['id'], 'status': 'failed', 'reason': '网络/格式异常或报价核对未通过'}
        quotes['quotes'].append(quote)
        (out / 'quotes.json').write_text(json.dumps(quotes, ensure_ascii=False, indent=2))
        print(f"核价 {index + 1}/{len(jobs)}：{quote['status']}", flush=True)
        if quote['status'] == 'failed':
            print('本轮停止，保留已完成结果；请核对失败原因后再继续。', flush=True)
            break
    result = rank_quotes(data, search, quotes)
    (out / 'report.txt').write_text(render(data, result))
    print(render(data, result))


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        sys.exit('\n已取消；未创建订单。')
    except (ValueError, RuntimeError, KeyError, TypeError, OSError):
        sys.exit('无法完成本轮核价，请检查快照时效、输入格式和本机连接。')
