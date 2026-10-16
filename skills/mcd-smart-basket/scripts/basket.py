#!/usr/bin/env python3
"""Bounded basket enumeration and quote ranking. Python 3.10+, no dependencies."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def integer(value, name, low=0, high=10**9):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} 必须为 {low}–{high} 的整数")
    return value


def counts(value, name, allow_empty=False):
    if not isinstance(value, dict) or (not value and not allow_empty):
        raise ValueError(f"{name} 必须为非空数量对象")
    for key, count in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"{name} 的键必须为非空字符串")
        integer(count, name, 1, 100)


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return result


def validate(snapshot):
    if snapshot.get("source") not in ("demo", "live"):
        raise ValueError("source 必须为 demo 或 live")
    ctx = snapshot["context"]
    if ctx.get("mode") not in ("pickup", "delivery") or not ctx.get("store_id"):
        raise ValueError("必须指定用餐方式及门店")
    if ctx.get("currency") != "CNY" or not ctx.get("service_time"):
        raise ValueError("必须指定 CNY 及用餐时间")
    if ctx["mode"] == "delivery" and not ctx.get("address_key"):
        raise ValueError("外送需要本地地址标识")
    timestamp(snapshot["captured_at"])
    counts(snapshot["demand"], "demand")
    integer(snapshot.get("max_extra_units", 0), "max_extra_units", 0, 10)
    limits = snapshot.get("coupon_limits", {})
    counts(limits, "coupon_limits", True)
    offers = snapshot["offers"]
    if not isinstance(offers, list) or not 1 <= len(offers) <= 80:
        raise ValueError("offers 需要 1–80 个选项；请先筛选相关餐品")
    seen = set()
    for offer in offers:
        if not isinstance(offer.get("id"), str) or not offer["id"] or offer["id"] in seen:
            raise ValueError("offer id 必须唯一且非空")
        seen.add(offer["id"])
        if not isinstance(offer.get("name"), str) or not offer["name"]:
            raise ValueError("offer name 不得为空")
        counts(offer["items"], "items")
        integer(offer["price_cents"], "price_cents")
        integer(offer["max_quantity"], "max_quantity", 0, 20)
        counts(offer.get("coupon_usage", {}), "coupon_usage", True)
        if not set(offer.get("coupon_usage", {})) <= set(limits):
            raise ValueError("优惠券用量引用了未声明的库存")
        groups = offer.get("exclusion_groups", [])
        if not isinstance(groups, list) or any(not isinstance(g, str) or not g for g in groups):
            raise ValueError("exclusion_groups 必须为字符串列表")
        if len(groups) != len(set(groups)):
            raise ValueError("互斥组不得重复")


def enumerate_plans(snapshot, max_nodes=200000, max_plans=5000):
    validate(snapshot)
    integer(max_nodes, "max_nodes", 1)
    integer(max_plans, "max_plans", 1)
    offers = sorted(snapshot["offers"], key=lambda o: o["id"])
    demand = Counter(snapshot["demand"])
    extra_limit = snapshot.get("max_extra_units", 0)
    sid = digest(snapshot)
    suffix = [Counter() for _ in range(len(offers) + 1)]
    for i in range(len(offers) - 1, -1, -1):
        suffix[i] = suffix[i + 1].copy()
        suffix[i].update({k: v * offers[i]["max_quantity"] for k, v in offers[i]["items"].items()})
    plans, nodes, stopped = [], 0, False

    def visit(i, totals, coupons, groups, cart, estimate):
        nonlocal nodes, stopped
        if stopped:
            return
        if nodes >= max_nodes:
            stopped = True
            return
        nodes += 1
        extras = {k: v - demand[k] for k, v in totals.items() if v > demand[k]}
        if sum(extras.values()) > extra_limit:
            return
        if any(totals[k] + suffix[i][k] < v for k, v in demand.items()):
            return
        if i == len(offers):
            if len(plans) >= max_plans:
                stopped = True
                return
            plans.append({"id": digest({"snapshot": sid, "cart": cart}),
                          "cart": list(cart), "items": dict(totals), "extras": extras,
                          "estimate_cents": estimate,
                          "single_item_baseline": all(
                              len(o["items"]) == 1 and sum(o["items"].values()) == 1
                              and not o.get("coupon_usage")
                              for o in offers if any(c["offer_id"] == o["id"] for c in cart)
                          ) and not extras})
            return
        offer = offers[i]
        for qty in range(offer["max_quantity"] + 1):
            new_coupons = coupons.copy()
            new_coupons.update({k: v * qty for k, v in offer.get("coupon_usage", {}).items()})
            if any(v > snapshot.get("coupon_limits", {}).get(k, 0) for k, v in new_coupons.items()):
                continue
            new_groups = groups.copy()
            new_groups.update({g: qty for g in offer.get("exclusion_groups", [])})
            if any(v > 1 for v in new_groups.values()):
                continue
            new_totals = totals.copy()
            new_totals.update({k: v * qty for k, v in offer["items"].items() if qty})
            new_cart = cart + ([{"offer_id": offer["id"], "quantity": qty}] if qty else [])
            visit(i + 1, new_totals, new_coupons, new_groups, new_cart,
                  estimate + offer["price_cents"] * qty)
            if stopped:
                break

    visit(0, Counter(), Counter(), Counter(), [], 0)
    plans.sort(key=lambda p: (p["estimate_cents"], sum(p["extras"].values()), p["id"]))
    return {"snapshot_id": sid, "source": snapshot["source"],
            "search_complete": not stopped, "visited_nodes": nodes,
            "scope": "仅限输入菜单、券、规格、数量上限和多买上限；不代表全店全渠道最低价",
            "plans": plans}


def validate_plan(snapshot, plan):
    """Rebuild each imported cart: do not trust edited totals, extras or baseline flags."""
    offers = {o["id"]: o for o in snapshot["offers"]}
    totals, coupons, groups, seen = Counter(), Counter(), Counter(), set()
    estimate, baseline = 0, True
    if not isinstance(plan["cart"], list) or not plan["cart"]:
        raise ValueError("候选购物车不能为空")
    for entry in plan["cart"]:
        oid, qty = entry["offer_id"], entry["quantity"]
        if oid in seen or oid not in offers:
            raise ValueError("购物车包含重复或未知选项")
        seen.add(oid)
        offer = offers[oid]
        integer(qty, "quantity", 1, offer["max_quantity"])
        totals.update({k: v * qty for k, v in offer["items"].items()})
        coupons.update({k: v * qty for k, v in offer.get("coupon_usage", {}).items()})
        groups.update({g: qty for g in offer.get("exclusion_groups", [])})
        estimate += offer["price_cents"] * qty
        baseline = baseline and len(offer["items"]) == 1 and sum(offer["items"].values()) == 1 and not offer.get("coupon_usage")
    demand = Counter(snapshot["demand"])
    extras = {k: v - demand[k] for k, v in totals.items() if v > demand[k]}
    if any(totals[k] < v for k, v in demand.items()) or sum(extras.values()) > snapshot.get("max_extra_units", 0):
        raise ValueError("购物车不满足餐品需求或多买上限")
    if any(v > snapshot.get("coupon_limits", {}).get(k, 0) for k, v in coupons.items()) or any(v > 1 for v in groups.values()):
        raise ValueError("购物车超过券库存或违反互斥规则")
    if plan["id"] != digest({"snapshot": digest(snapshot), "cart": plan["cart"]}):
        raise ValueError("购物车内容与方案指纹不匹配")
    return dict(plan, items=dict(totals), extras=extras, estimate_cents=estimate,
                single_item_baseline=bool(baseline and not extras))


def rank_quotes(snapshot, search, quote_file, now=None):
    validate(snapshot)
    now = now or datetime.now(timezone.utc)
    sid = digest(snapshot)
    if search["snapshot_id"] != sid or quote_file.get("snapshot_id") != sid:
        raise ValueError("报价或候选与本次门店、用餐方式、地址、时间或菜单不匹配")
    if quote_file.get("source") != snapshot["source"]:
        raise ValueError("模拟与真实数据不能混用")
    snapshot_age = (now - timestamp(snapshot["captured_at"])).total_seconds()
    if not -30 <= snapshot_age <= 900:
        raise ValueError("菜单快照已过期或来自未来，请重新查询")
    by_id = {p["id"]: validate_plan(snapshot, p) for p in search["plans"]}
    if len(by_id) != len(search["plans"]):
        raise ValueError("候选方案重复")
    seen, valid, invalid = set(), [], []
    for quote in quote_file["quotes"]:
        pid = quote["plan_id"]
        if pid in seen or pid not in by_id:
            raise ValueError("报价存在重复或未知方案")
        seen.add(pid)
        if quote.get("status") == "rejected":
            invalid.append({"plan_id": pid, "reason": quote.get("reason", "官方未通过")})
            continue
        if quote.get("status") != "accepted":
            invalid.append({"plan_id": pid, "reason": "计价失败或未完成"})
            continue
        age = (now - timestamp(quote["quoted_at"])).total_seconds()
        if not -30 <= age <= 300:
            invalid.append({"plan_id": pid, "reason": "报价超过5分钟或时间无效"})
            continue
        if not quote.get("evidence_ref"):
            raise ValueError("接受的报价需要本地原始响应凭据引用")
        for field in ("total_cents", "delivery_fee_cents", "packaging_fee_cents"):
            integer(quote[field], field)
        if snapshot["context"]["mode"] == "pickup" and quote["delivery_fee_cents"]:
            raise ValueError("到店自取报价不能含配送费")
        valid.append(dict(by_id[pid], quote=quote))
    valid.sort(key=lambda p: (p["quote"]["total_cents"], sum(p["extras"].values()), p["id"]))
    exact = [p for p in valid if not p["extras"]]
    extra = [p for p in valid if p["extras"]]
    baselines = [p for p in exact if p["single_item_baseline"]]
    return {"source": snapshot["source"], "context": snapshot["context"],
            "scope": search["scope"], "search_complete": search["search_complete"],
            "candidate_count": len(by_id), "priced_count": len(valid),
            "unquoted_count": len(by_id) - len(seen), "invalid": invalid,
            "exact": exact, "extra": extra,
            "baseline_cents": baselines[0]["quote"]["total_cents"] if baselines else None}


def render(snapshot, result):
    money = lambda cents: f"¥{cents / 100:.2f}"
    names = {o["id"]: o["name"] for o in snapshot["offers"]}
    labels = snapshot.get("labels", {})
    mode = "到店自取" if result["context"]["mode"] == "pickup" else "外送"
    tag = "模拟演示 · 非实际价格" if result["source"] == "demo" else "真实数据 · 官方计价结果由调用方导入"
    lines = [f"麦麦省心凑单｜{mode}", tag,
             f"门店：{result['context']['store_id']}｜用餐时间：{result['context']['service_time']}",
             f"菜单查询时间：{snapshot['captured_at']}", "",
             "想吃：" + "、".join(f"{labels.get(k, k)} × {v}" for k, v in snapshot["demand"].items())]
    exact_best = result["exact"][0]["quote"]["total_cents"] if result["exact"] else None
    cheaper_extras = [p for p in result["extra"] if exact_best is None or p["quote"]["total_cents"] < exact_best]
    for title, choices in (("刚好买齐", result["exact"]), ("多买更便宜（不自动替换）" if exact_best is not None else "多买候选（没有刚好买齐报价可比较）", cheaper_extras)):
        lines.extend(["", title])
        if not choices:
            lines.append("暂无比刚好买齐更便宜的多买方案。" if title.startswith("多买更便宜") else "暂无通过计价的方案。")
        for idx, plan in enumerate(choices[:3], 1):
            quote = plan["quote"]
            cart = " + ".join(f"{names[c['offer_id']]} × {c['quantity']}" for c in plan["cart"])
            lines.append(f"{idx}. {cart}")
            lines.append(f"   应付 {money(quote['total_cents'])}（已含配送费 {money(quote['delivery_fee_cents'])}、包装费 {money(quote['packaging_fee_cents'])}）")
            if plan["extras"]:
                lines.append("   多出：" + "、".join(f"{labels.get(k, k)} × {v}" for k, v in plan["extras"].items()))
                if exact_best is not None:
                    lines.append(f"   相比最便宜的刚好买齐方案，再省 {money(exact_best - quote['total_cents'])}")
            if result["baseline_cents"] is not None:
                delta = result["baseline_cents"] - quote["total_cents"]
                lines.append(f"   对比同场景单点：{'省' if delta >= 0 else '贵'} {money(abs(delta))}")
    if result["baseline_cents"] is None:
        lines.append("\n单点基准未核价，不计算省了多少钱。")
    lines.extend(["", f"找到 {result['candidate_count']} 个候选，通过计价 {result['priced_count']} 个，未核价 {result['unquoted_count']} 个，失效/拒绝 {len(result['invalid'])} 个。",
                  "搜索完成。" if result["search_complete"] else "搜索达到上限，候选未穷尽。",
                  "仅推荐已比较且通过计价的方案中最便宜的买法。", result["scope"],
                  "未下单、未付款。下单前需重新核价。"])
    return "\n".join(lines)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="麦麦省心凑单：候选生成 / 官方整单报价排序")
    sub = parser.add_subparsers(dest="command", required=True)
    enum = sub.add_parser("enumerate")
    enum.add_argument("snapshot")
    enum.add_argument("--max-nodes", type=int, default=200000)
    enum.add_argument("--max-plans", type=int, default=5000)
    rank = sub.add_parser("rank")
    rank.add_argument("snapshot")
    rank.add_argument("search")
    rank.add_argument("quotes")
    rank.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        snapshot = read(args.snapshot)
        if args.command == "enumerate":
            print(json.dumps(enumerate_plans(snapshot, args.max_nodes, args.max_plans), ensure_ascii=False, indent=2))
        else:
            result = rank_quotes(snapshot, read(args.search), read(args.quotes))
            print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(snapshot, result))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f"输入错误：{exc}\n")


if __name__ == "__main__":
    main()
