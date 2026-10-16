import copy
from pathlib import Path
import sys
import unittest
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'skills/mcd-smart-basket/scripts'), str(Path(__file__).resolve().parents[1] / 'examples')]
from mcd_adapter import normalize_quote, build_price_arguments
from basket import enumerate_plans
from demo import snapshot


class AdapterTests(unittest.TestCase):
    def fixture(self):
        args = {'beType': 2, 'items': [{'productCode': 'A', 'quantity': 1}]}
        result = {'structuredContent': {'success': True, 'datetime': '2026-10-16 12:00:00', 'data': {
            'price': 1870, 'productPrice': 1000, 'deliveryPrice': 600, 'packingPrice': 270,
            'productList': [{'productCode': 'A', 'quantity': 1, 'productName': '测试食品'},
                            {'productCode': 'FEE', 'quantity': 27, 'productName': '打包服务费'}]}}}
        return args, result

    def test_delivery_fee_not_counted_twice(self):
        args, result = self.fixture()
        quote = normalize_quote('p', args, result, 'private/raw.json')
        self.assertEqual(quote['total_cents'], 1870)
        self.assertEqual(quote['packaging_fee_cents'], 270)

    def test_missing_or_changed_food_is_rejected(self):
        args, result = self.fixture()
        for product_list in ([], [{'productCode': 'A', 'quantity': 2}], [{'productCode': 'B', 'quantity': 1}]):
            modified = copy.deepcopy(result)
            modified['structuredContent']['data']['productList'] = product_list
            with self.assertRaises(ValueError):
                normalize_quote('p', args, modified, 'private/raw.json')

    def test_unknown_added_product_is_rejected(self):
        args, result = self.fixture()
        result['structuredContent']['data']['productList'].append({'productCode': 'X', 'quantity': 1, 'productName': '附加食品'})
        with self.assertRaises(ValueError):
            normalize_quote('p', args, result, 'private/raw.json')

    def test_missing_delivery_fees_not_assumed_zero(self):
        args, result = self.fixture()
        del result['structuredContent']['data']['deliveryPrice']
        with self.assertRaises(ValueError):
            normalize_quote('p', args, result, 'private/raw.json')

    def test_inconsistent_totals_require_review(self):
        args, result = self.fixture()
        result['structuredContent']['data']['price'] = 1800
        with self.assertRaises(ValueError):
            normalize_quote('p', args, result, 'private/raw.json')

    def test_business_rejection_not_a_free_quote(self):
        args, result = self.fixture()
        result['structuredContent']['success'] = False
        self.assertEqual(normalize_quote('p', args, result, 'raw')['status'], 'rejected')

    def test_live_request_uses_mode_and_bound_business_code(self):
        for mode in ('pickup', 'delivery'):
            data = snapshot(mode)
            data['source'] = 'live'
            data['context']['be_code'] = 'test-business-code'
            for o in data['offers']:
                o['official_selection'] = {'productCode': o['id']}
                if o.get('coupon_usage'):
                    o['official_selection'].update(couponId='test-id', couponCode='test-code')
            search = enumerate_plans(data)
            request = build_price_arguments(data, search['plans'][0])
            self.assertEqual(request['beType'], 1 if mode == 'pickup' else 2)
            self.assertEqual('beCode' in request, mode == 'delivery')
            self.assertNotIn('addressId', request)

    def test_demo_snapshot_never_sent_live(self):
        data = snapshot('pickup')
        with self.assertRaises(ValueError):
            build_price_arguments(data, enumerate_plans(data)['plans'][0])


if __name__ == '__main__':
    unittest.main()
