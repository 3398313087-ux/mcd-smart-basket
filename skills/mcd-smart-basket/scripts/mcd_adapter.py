"""Mapping verified against the official MCP schemas and live replies on 2026-10-16."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from basket import integer, validate, validate_plan


def build_price_arguments(snapshot, plan):
    validate(snapshot)
    if snapshot['source'] != 'live':
        raise ValueError('模拟快照不能发送到真实计价接口')
    validate_plan(snapshot, plan)
    context = snapshot['context']
    delivery = context['mode'] == 'delivery'
    args = {'storeCode': context['store_id'], 'orderType': 2 if delivery else 1,
            'beType': 2 if delivery else 1, 'needTableware': context.get('need_tableware', False)}
    if delivery:
        if not context.get('be_code'):
            raise ValueError('外送缺少该地址查店返回的 beCode')
        args['beCode'] = context['be_code']
    if context['service_time'] != 'immediate':
        if not context.get('reservation_date'):
            raise ValueError('预约场景需要官方格式 reservation_date')
        args['reservationDate'] = context['reservation_date']
    offers = {o['id']: o for o in snapshot['offers']}
    items = []
    for line in plan['cart']:
        offer = offers[line['offer_id']]
        item = deepcopy(offer.get('official_selection'))
        if not isinstance(item, dict) or not item.get('productCode'):
            raise ValueError('缺少已核实的官方商品配置')
        if set(item) - {'productCode', 'roundList', 'modification', 'couponId', 'couponCode'}:
            raise ValueError('商品配置包含不支持的字段')
        if bool(item.get('couponId')) != bool(item.get('couponCode')):
            raise ValueError('券 ID 和券码必须同时提供')
        if bool(offer.get('coupon_usage')) != bool(item.get('couponId')):
            raise ValueError('券库存模型与官方券配置不匹配')
        item['quantity'] = line['quantity']
        items.append(item)
    args['items'] = items
    return args


def normalize_quote(plan_id, arguments, response, evidence_ref):
    if response.get('isError'):
        return {'plan_id': plan_id, 'status': 'failed', 'reason': 'MCP 工具执行失败'}
    business = response.get('structuredContent')
    if not isinstance(business, dict):
        raise ValueError('缺少官方结构化响应，不能自动导入报价')
    if business.get('success') is not True:
        return {'plan_id': plan_id, 'status': 'rejected', 'reason': '官方业务未通过计价'}
    data = business['data']
    expected = Counter()
    for item in arguments['items']:
        expected[item['productCode']] += item['quantity']
    returned = Counter()
    for item in data['productList']:
        integer(item['quantity'], '商品数量', 1)
        if item['productCode'] not in expected:
            # The live delivery reply includes an explicit packing fee line in productList.
            # Do not treat fees as food or silently allow unknown products.
            if item.get('productName') == '打包服务费' and 'packingPrice' in data:
                continue
            raise ValueError('官方结果含未请求的商品，需人工核对')
        returned[item['productCode']] += item['quantity']
    if returned != expected:
        raise ValueError('官方返回的商品编号或数量与购物车不一致')
    if arguments['beType'] == 2 and not {'deliveryPrice', 'packingPrice'} <= data.keys():
        raise ValueError('外送报价缺少费用字段，不能假设为零')
    delivery, packing, tableware = (data.get(k, 0) for k in ('deliveryPrice', 'packingPrice', 'tablewarePrice'))
    for value in (data['price'], data['productPrice'], delivery, packing, tableware):
        integer(value, '官方金额')
    if data['price'] != data['productPrice'] + delivery + packing + tableware:
        raise ValueError('应付总额与商品及费用明细不一致，需人工核对')
    if arguments['beType'] == 1 and delivery:
        raise ValueError('自取响应意外包含配送费')
    quoted_at = datetime.strptime(business['datetime'], '%Y-%m-%d %H:%M:%S').replace(
        tzinfo=timezone(timedelta(hours=8))).isoformat()
    return {'plan_id': plan_id, 'status': 'accepted', 'total_cents': data['price'],
            'delivery_fee_cents': delivery, 'packaging_fee_cents': packing,
            'tableware_fee_cents': tableware, 'quoted_at': quoted_at, 'evidence_ref': evidence_ref,
            'selection_verification': '商品编号数量已回显核对；套餐子项绑定原始请求，官方报价不逐项回显'}
