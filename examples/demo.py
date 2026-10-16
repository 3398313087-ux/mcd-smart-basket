#!/usr/bin/env python3
"""Fully synthetic fixtures; no network, actual McDonald's prices or account data."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/mcd-smart-basket/scripts"))
from basket import enumerate_plans, rank_quotes, render


def snapshot(mode):
    def offer(id, name, items, cents, coupon=None):
        row = {"id": id, "name": name, "items": items, "price_cents": cents, "max_quantity": 2}
        if coupon:
            row["coupon_usage"] = {coupon: 1}
        return row
    return {
        "source": "demo", "captured_at": datetime.now(timezone.utc).isoformat(),
        "context": {"mode": mode, "store_id": "DEMO-STORE", "currency": "CNY",
                    "address_key": "DEMO-ADDRESS" if mode == "delivery" else None, "service_time": "immediate"},
        "demand": {"burger": 1, "fries-medium": 1, "cola-medium": 1, "nuggets-5": 1},
        "labels": {"burger": "指定汉堡", "fries-medium": "中薯", "cola-medium": "中杯可乐",
                   "nuggets-5": "5块鸡块", "pie": "派"},
        "max_extra_units": 1, "coupon_limits": {"coupon-A": 1},
        "offers": [
            offer("single-burger", "汉堡单点", {"burger": 1}, 2500),
            offer("single-fries", "中薯单点", {"fries-medium": 1}, 1200),
            offer("single-cola", "中杯可乐单点", {"cola-medium": 1}, 1000),
            offer("single-nuggets", "5块鸡块单点", {"nuggets-5": 1}, 1300),
            offer("meal-basic", "汉堡中薯可乐套餐", {"burger": 1, "fries-medium": 1, "cola-medium": 1}, 3300),
            offer("meal-coupon", "已有券：汉堡中薯可乐套餐", {"burger": 1, "fries-medium": 1, "cola-medium": 1}, 2600, "coupon-A"),
            offer("meal-plus", "四件餐加派组合", {"burger": 1, "fries-medium": 1, "cola-medium": 1, "nuggets-5": 1, "pie": 1}, 4500),
        ]}


def simulate_quotes(data, search):
    quotes = []
    for plan in search["plans"]:
        subtotal = plan["estimate_cents"]
        delivery = 900 if data["context"]["mode"] == "delivery" and subtotal < 4500 else 0
        packaging = 100 if data["context"]["mode"] == "delivery" else 0
        quotes.append({"plan_id": plan["id"], "status": "accepted", "total_cents": subtotal + delivery + packaging,
                       "delivery_fee_cents": delivery, "packaging_fee_cents": packaging,
                       "quoted_at": data["captured_at"], "evidence_ref": "synthetic-demo-only"})
    return {"source": "demo", "snapshot_id": search["snapshot_id"], "quotes": quotes}


if __name__ == "__main__":
    for mode in ("pickup", "delivery"):
        data = snapshot(mode)
        search = enumerate_plans(data)
        result = rank_quotes(data, search, simulate_quotes(data, search))
        print(render(data, result))
        print("\n" + "─" * 50 + "\n")
