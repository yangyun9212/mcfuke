"""Check the internal consistency of a WorkBuddy-exported validation report.

This does not authenticate the remote service or replace the original call trace.
Output omits store names, codes, trace IDs and raw request/response bodies.
"""

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys


READ_TOOLS = frozenset(("now-time-info", "query-nearby-stores", "query-meals",
                       "query-meal-detail", "query-store-coupons", "calculate-price"))


def audit_report(data):
    errors = []
    warnings = []
    summaries = []

    def check(condition, label):
        if not condition:
            errors.append(label)

    def money(value):
        return type(value) is int and value >= 0

    if not isinstance(data, dict):
        return {"consistent": False, "errors": ["report_not_object"], "remote_authenticated": False}
    calls = data.get("callLog", [])
    stores = data.get("stores", [])
    if (not isinstance(calls, list) or not calls or any(not isinstance(c, dict) for c in calls)
            or not isinstance(stores, list) or len(stores) != 2
            or any(not isinstance(s, dict) for s in stores)):
        return {"consistent": False, "errors": ["expected_call_log_and_two_stores"],
                "remote_authenticated": False}
    check(data.get("format") == "mcfuke.live-validation.local.v1", "unsupported_report_format")
    check(all(c.get("tool") in READ_TOOLS for c in calls), "unexpected_or_writing_tool_in_log")
    check(all(c.get("status") == "success" for c in calls), "unsuccessful_call_in_completed_report")
    check(all(isinstance(c.get("input"), dict) and isinstance(c.get("outputCore"), dict)
              for c in calls), "invalid_call_payload")
    if "invalid_call_payload" in errors:
        return {"consistent": False, "errors": errors, "remote_authenticated": False}
    steps = [c.get("step") for c in calls]
    check(all(isinstance(s, str) and s for s in steps) and len(set(steps)) == len(steps),
          "missing_or_duplicate_step")
    codes = [s.get("storeCode") for s in stores]
    check(all(isinstance(s, str) and s for s in codes) and len(set(codes)) == 2,
          "stores_not_distinct")
    conditions = data.get("confirmedConditions", {})
    budget = conditions.get("budget", {}).get("limitFen") if isinstance(conditions, dict) else None
    check(money(budget), "invalid_budget")

    for index, store in enumerate(stores):
        label = f"store_{index + 1}"
        code = store.get("storeCode")
        own = [c for c in calls if c["input"].get("storeCode") == code]
        check(store.get("completionStatus") == "completed", f"{label}:not_completed")
        for tool in ("query-meals", "query-meal-detail", "query-store-coupons", "calculate-price"):
            check(any(c.get("tool") == tool for c in own), f"{label}:missing_{tool}")
        pricing = store.get("priceResult", {})
        if not isinstance(pricing, dict):
            errors.append(f"{label}:invalid_price_result")
            continue
        total, original, discount = (pricing.get(key) for key in
                                     ("priceFen", "originalPriceFen", "discountFen"))
        check(all(money(v) for v in (total, original, discount)), f"{label}:invalid_money")
        if all(money(v) for v in (total, original, discount)):
            check(original - discount == total, f"{label}:price_arithmetic_mismatch")
        try:
            converted = Decimal(str(pricing.get("priceYuan"))) * 100
            check(converted.is_finite() and converted == total, f"{label}:yuan_fen_mismatch")
        except (InvalidOperation, TypeError):
            errors.append(f"{label}:invalid_yuan")
        if money(total) and money(budget):
            check(pricing.get("withinBudget") is (total <= budget), f"{label}:budget_claim_mismatch")
        quote_calls = [c for c in own if c.get("tool") == "calculate-price"
                       and c["outputCore"].get("priceFen") == total]
        check(bool(quote_calls), f"{label}:missing_matching_quote")
        if not quote_calls:
            continue
        quote_call = quote_calls[-1]
        for key in ("originalPriceFen", "discountFen"):
            check(quote_call["outputCore"].get(key) == pricing.get(key), f"{label}:{key}_mismatch")
        check(quote_call["input"].get("beType") == 1
              and quote_call["input"].get("orderType") == 1, f"{label}:scene_mismatch")
        check(quote_call.get("localTime") == store.get("pricedAt"), f"{label}:quote_time_mismatch")
        request_items = quote_call["input"].get("items", [])
        response_items = pricing.get("productListFromResponse", [])
        if (not isinstance(request_items, list) or not request_items
                or not isinstance(response_items, list) or not response_items
                or any(not isinstance(i, dict) for i in request_items + response_items)):
            errors.append(f"{label}:missing_itemized_cart")
            continue
        quantities_valid = all(isinstance(i.get("productCode"), str)
                               and type(i.get("quantity")) is int and i["quantity"] > 0
                               for i in request_items + response_items)
        check(quantities_valid, f"{label}:invalid_item_identity_or_quantity")
        if quantities_valid:
            # Aggregate product quantities while preserving repeated lines.
            def quantities(items):
                result = Counter()
                for item in items:
                    result[item["productCode"]] += item["quantity"]
                return result
            check(quantities(request_items) == quantities(response_items), f"{label}:cart_echo_mismatch")
        details = {c["input"].get("code") for c in own if c.get("tool") == "query-meal-detail"}
        check(all(i.get("productCode") in details for i in request_items), f"{label}:missing_own_store_detail")
        subtotals = [i.get("subtotalFen") for i in response_items]
        check(all(money(v) for v in subtotals), f"{label}:invalid_subtotal")
        if all(money(v) for v in subtotals):
            check(sum(subtotals) == pricing.get("productPriceFen"), f"{label}:product_subtotal_mismatch")
        summaries.append({"store": label, "total_fen": total,
                          "priced_at": store.get("pricedAt"),
                          "replacement_count": len(store.get("substituted", []))})

    if not data.get("rawResponsePersistence", {}).get("persisted"):
        warnings.append("summary_export_only_no_verbatim_responses")
    if not any(s.get("substituted") for s in stores):
        warnings.append("replacement_branch_not_exercised")
    warnings.extend(("same_account_only", "no_order_or_payment_validation",
                     "option_intent_not_proven_by_total_price_alone"))
    return {
        "consistent": not errors, "remote_authenticated": False,
        "evidence_type": "workbuddy_exported_core_fields",
        "call_count": len(calls), "store_count": len(stores),
        "tool_counts": dict(Counter(c.get("tool") for c in calls)),
        "stores": summaries, "errors": errors, "warnings": warnings,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = audit_report(json.loads(args.report.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print('{"consistent": false, "errors": ["unreadable_or_malformed_report"]}')
        return 2
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    print(output)
    return 0 if result["consistent"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
