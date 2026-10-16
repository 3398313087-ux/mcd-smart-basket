import copy
from datetime import datetime, timedelta, timezone
import itertools
import json
from pathlib import Path
import random
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "skills/mcd-smart-basket/scripts"), str(ROOT / "examples")]
from basket import enumerate_plans, rank_quotes, render
from demo import snapshot, simulate_quotes


class BasketTests(unittest.TestCase):
    def result(self, mode):
        data = snapshot(mode)
        search = enumerate_plans(data)
        return data, search, rank_quotes(data, search, simulate_quotes(data, search))

    def test_pickup_coupon_and_exact_contents(self):
        data, search, result = self.result("pickup")
        self.assertEqual(result["exact"][0]["quote"]["total_cents"], 3900)
        self.assertEqual(result["exact"][0]["items"], data["demand"])
        self.assertEqual(result["baseline_cents"], 6000)
        self.assertTrue(search["search_complete"])

    def test_delivery_uses_final_total_not_menu_estimate(self):
        _, _, result = self.result("delivery")
        self.assertEqual(result["exact"][0]["quote"]["total_cents"], 4700)
        self.assertGreater(result["exact"][0]["estimate_cents"], result["exact"][1]["estimate_cents"])
        self.assertEqual(result["extra"][0]["quote"]["total_cents"], 4600)
        self.assertEqual(result["extra"][0]["extras"], {"pie": 1})

    def test_extra_is_opt_in(self):
        data = snapshot("pickup")
        data["max_extra_units"] = 0
        self.assertTrue(all(not p["extras"] for p in enumerate_plans(data)["plans"]))

    def test_display_only_recommends_extras_that_save_money(self):
        data, _, result = self.result("pickup")
        self.assertNotIn("四件餐加派组合", render(data, result))
        data, _, result = self.result("delivery")
        self.assertIn("再省 ¥1.00", render(data, result))

    def test_coupon_inventory_shared_across_variants(self):
        data = snapshot("pickup")
        data["demand"] = {k: 2 for k in data["demand"]}
        variant = copy.deepcopy(data["offers"][-2])
        variant["id"] = "coupon-variant"
        data["offers"].append(variant)
        for p in enumerate_plans(data)["plans"]:
            uses = sum(c["quantity"] for c in p["cart"] if c["offer_id"] in ("meal-coupon", "coupon-variant"))
            self.assertLessEqual(uses, 1)

    def test_mutually_exclusive_offers(self):
        data = snapshot("pickup")
        for o in data["offers"]:
            if o["id"] in ("single-nuggets", "meal-coupon"):
                o["exclusion_groups"] = ["cannot-stack"]
        for p in enumerate_plans(data)["plans"]:
            ids = {c["offer_id"] for c in p["cart"]}
            self.assertFalse({"single-nuggets", "meal-coupon"} <= ids)

    def test_unavailable_item_produces_no_fake_result(self):
        data = snapshot("pickup")
        data["demand"]["not-on-menu"] = 1
        self.assertEqual(enumerate_plans(data)["plans"], [])

    def test_no_automatic_size_substitution(self):
        data = snapshot("pickup")
        data["demand"]["fries-large"] = data["demand"].pop("fries-medium")
        self.assertEqual(enumerate_plans(data)["plans"], [])

    def test_search_caps_disclosed(self):
        data = snapshot("pickup")
        self.assertFalse(enumerate_plans(data, max_nodes=2)["search_complete"])
        result = enumerate_plans(data, max_plans=1)
        self.assertFalse(result["search_complete"])
        self.assertEqual(len(result["plans"]), 1)

    def test_reject_wrong_context(self):
        data = snapshot("delivery")
        search = enumerate_plans(data)
        quotes = simulate_quotes(data, search)
        for key, value in (("store_id", "other"), ("address_key", "other"), ("mode", "pickup"), ("service_time", "tomorrow")):
            modified = copy.deepcopy(data)
            modified["context"][key] = value
            with self.assertRaises(ValueError):
                rank_quotes(modified, search, quotes)

    def test_no_mixed_demo_and_live(self):
        data = snapshot("pickup")
        search = enumerate_plans(data)
        quotes = simulate_quotes(data, search)
        quotes["source"] = "live"
        with self.assertRaises(ValueError):
            rank_quotes(data, search, quotes)

    def test_expired_quotes_and_failed_quotes_excluded(self):
        data = snapshot("pickup")
        search = enumerate_plans(data)
        quotes = simulate_quotes(data, search)
        past = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
        for q in quotes["quotes"]:
            q["quoted_at"] = past
        quotes["quotes"][0]["status"] = "rejected"
        result = rank_quotes(data, search, quotes)
        self.assertEqual(result["priced_count"], 0)
        self.assertEqual(result["baseline_cents"], None)
        self.assertEqual(len(result["invalid"]), len(search["plans"]))

    def test_partial_quotes_do_not_claim_baseline(self):
        data = snapshot("pickup")
        search = enumerate_plans(data)
        quotes = simulate_quotes(data, search)
        quotes["quotes"] = quotes["quotes"][:1]
        result = rank_quotes(data, search, quotes)
        self.assertEqual(result["unquoted_count"], len(search["plans"]) - 1)
        self.assertIsNone(result["baseline_cents"])
        self.assertIn("基准未核价", render(data, result))

    def test_duplicate_quote_rejected(self):
        data = snapshot("pickup")
        search = enumerate_plans(data)
        quotes = simulate_quotes(data, search)
        quotes["quotes"].append(quotes["quotes"][0])
        with self.assertRaises(ValueError):
            rank_quotes(data, search, quotes)

    def test_stale_menu_rejected(self):
        data = snapshot("pickup")
        data["captured_at"] = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        search = enumerate_plans(data)
        with self.assertRaises(ValueError):
            rank_quotes(data, search, simulate_quotes(data, search))

    def test_edited_cart_rejected(self):
        data = snapshot("pickup")
        search = enumerate_plans(data)
        quotes = simulate_quotes(data, search)
        search["plans"][0]["cart"][0]["quantity"] = 2
        with self.assertRaises(ValueError):
            rank_quotes(data, search, quotes)

    def test_totals_rebuilt_not_trusted(self):
        data = snapshot("pickup")
        search = enumerate_plans(data)
        quotes = simulate_quotes(data, search)
        for p in search["plans"]:
            p["extras"] = {}
            p["single_item_baseline"] = True
        result = rank_quotes(data, search, quotes)
        self.assertEqual(result["baseline_cents"], 6000)
        self.assertTrue(result["extra"])

    def test_negative_and_fractional_prices_rejected(self):
        for bad in (-1, 1.5, True):
            data = snapshot("pickup")
            data["offers"][0]["price_cents"] = bad
            with self.assertRaises(ValueError):
                enumerate_plans(data)

    def test_small_random_catalogs_match_independent_bruteforce(self):
        rng = random.Random(20261015)
        for _ in range(40):
            data = snapshot("pickup")
            data["demand"] = {"a": rng.randint(1, 3), "b": rng.randint(1, 3)}
            data["max_extra_units"] = rng.randint(0, 2)
            data["offers"] = [{"id": str(i), "name": str(i), "items": {rng.choice(["a", "b"]): rng.randint(1, 2)},
                               "price_cents": rng.randint(0, 1000), "max_quantity": 3} for i in range(4)]
            expected = set()
            for quantities in itertools.product(range(4), repeat=4):
                totals = {k: sum(q * o["items"].get(k, 0) for q, o in zip(quantities, data["offers"])) for k in ("a", "b")}
                if all(totals[k] >= data["demand"][k] for k in totals) and sum(totals[k] - data["demand"][k] for k in totals) <= data["max_extra_units"]:
                    expected.add(quantities)
            search = enumerate_plans(data)
            actual = set()
            for p in search["plans"]:
                quantities = {c["offer_id"]: c["quantity"] for c in p["cart"]}
                actual.add(tuple(quantities.get(str(i), 0) for i in range(4)))
            self.assertTrue(search["search_complete"])
            self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
