"""Native-MCP handoff: prepare exact requests, then assess matching receipts.

The host agent obtains current details and invokes MCP. This module has no network
or order creation code. Mode labels and local JSON are not authentication proofs.
"""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import zipfile

from .recipe import RecipeError, _integer, _keys, _text, export_recipe, fingerprint, resolve_recipe, validate_recipe


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise RecipeError("时间需为含时区的ISO8601格式") from None
    if parsed.tzinfo is None:
        raise RecipeError("时间需带时区")
    return parsed


def request_item(value):
    _keys(value, ("productCode", "quantity"), ("modification", "roundList"))
    _text(value["productCode"])
    _integer(value["quantity"], 1, 20)
    if "modification" in value:
        _keys(value["modification"], ("values",))
        values = value["modification"]["values"]
        if not isinstance(values, list) or len(values) > 30:
            raise RecipeError("特调项格式错误")
        for modifier in values:
            _keys(modifier, ("code", "key", "quantity"))
            _text(modifier["code"])
            _text(modifier["key"])
            _integer(modifier["quantity"], 0, 20)
    if "roundList" in value:
        rounds = value["roundList"]
        if not isinstance(rounds, list) or not 1 <= len(rounds) <= 20:
            raise RecipeError("选配轮次格式错误")
        for round_value in rounds:
            _keys(round_value, ("round", "comboItemList"))
            _text(round_value["round"])
            children = round_value["comboItemList"]
            if not isinstance(children, list) or not 1 <= len(children) <= 20:
                raise RecipeError("子选项格式错误")
            for child in children:
                _keys(child, ("code", "quantity"), ("modification",))
                request_item({"productCode": child["code"], **{k: v for k, v in child.items() if k != "code"}})
    return deepcopy(value)


def prepare_job(recipe, bindings):
    recipe = validate_recipe(recipe)
    _keys(bindings, ("format", "mode", "observed_at", "store", "menu"))
    if bindings["format"] != "mcfuke.bindings.v1" or bindings["mode"] not in ("live", "recorded", "simulated"):
        raise RecipeError("绑定格式或数据模式错误")
    timestamp(bindings["observed_at"])
    _keys(bindings["store"], ("code", "name"))
    for value in bindings["store"].values():
        _text(value)
    menu = bindings["menu"]
    if not isinstance(menu, list) or not 1 <= len(menu) <= 1000:
        raise RecipeError("绑定菜单格式错误")
    by_sku = {}
    for item in menu:
        _keys(item, ("sku", "name", "options", "available", "request_item", "detail_reference", "option_echo"))
        _text(item["sku"])
        _text(item["name"])
        _text(item["detail_reference"])
        if item["sku"] in by_sku or type(item["available"]) is not bool:
            raise RecipeError("绑定ID重复或可售状态未知")
        if item["option_echo"] not in ("unknown", "confirmed", "not_applicable"):
            raise RecipeError("选配回显状态错误")
        request_item(item["request_item"])
        if item["request_item"]["quantity"] != 1:
            raise RecipeError("绑定需描述一份餐品，最终份数来自配方")
        by_sku[item["sku"]] = item
    resolved = resolve_recipe(recipe, menu)
    job = {"format": "mcfuke.quote-job.v1", "mode": bindings["mode"],
           "observed_at": bindings["observed_at"], "recipe": recipe,
           "store": deepcopy(bindings["store"]), "orderable": False,
           "status": resolved["status"], "requests": []}
    if resolved["status"] != "resolved":
        job.update(resolved)
        job["job_fingerprint"] = fingerprint(job)
        return job
    for candidate in resolved["candidates"]:
        items = []
        echo_states = []
        detail_refs = []
        for choice in candidate["cart"]:
            binding = by_sku[choice["sku"]]
            official = deepcopy(binding["request_item"])
            official["quantity"] = choice["quantity"]
            items.append(official)
            echo_states.append(binding["option_echo"])
            detail_refs.append(binding["detail_reference"])
        request = {"storeCode": bindings["store"]["code"], "orderType": 1, "beType": 1,
                   "needTableware": False, "items": items}
        job["requests"].append({"request_fingerprint": fingerprint(request), "request": request,
                                "preference_rank": candidate["preference_rank"],
                                "cart": candidate["cart"], "differences": candidate["differences"],
                                "option_echo": "unknown" if any(c["options"] for c in candidate["cart"])
                                or any(i.get("roundList") for i in items) else "not_applicable",
                                "detail_references": detail_refs})
    job.update({"status": "awaiting_quotes", "search_truncated": resolved["search_truncated"]})
    job["job_fingerprint"] = fingerprint(job)
    return job


def assess_job(job, receipts, now=None, current_intent=None, confirmation=None):
    if job.get("format") != "mcfuke.quote-job.v1" or job.get("status") not in ("awaiting_quotes", "needs_input"):
        raise RecipeError("任务尚未准备好核价")
    unsigned = {k: v for k, v in job.items() if k != "job_fingerprint"}
    if fingerprint(unsigned) != job.get("job_fingerprint"):
        raise RecipeError("任务内容已变更，需重新生成候选")
    from .intent import check_job_conditions
    check_job_conditions(job, current_intent, confirmation)
    _keys(receipts, ("format", "mode", "quotes"))
    if receipts["format"] != "mcfuke.receipts.v1" or receipts["mode"] != job["mode"]:
        raise RecipeError("报价模式与任务不一致")
    quotes = receipts["quotes"]
    if not isinstance(quotes, list) or len(quotes) > 5:
        raise RecipeError("报价数量错误")
    if job["status"] == "needs_input":
        # Unavailable approved items are a normal business stop, not a failed CLI.
        recipe = validate_recipe(job["recipe"])
        labels = {slot["label"] for slot in recipe["slots"]}
        if (job.get("reason") != "no_approved_match" or job.get("missing_slot") not in labels
                or job.get("requests") != [] or quotes):
            raise RecipeError("不可售任务不能包含核价请求或报价")
        observed_at = timestamp(job["observed_at"])
        if job["mode"] == "live":
            age = ((now or datetime.now(timezone.utc)) - observed_at).total_seconds()
            if not 0 <= age <= 600:
                raise RecipeError("门店资料已过本地复核窗口，需重新查询")
        return {"mode": job["mode"], "provenance_authenticated": False,
                "orderable": False, "order_created": False,
                "status": "needs_input", "reason": "no_approved_match",
                "observed_at": job["observed_at"], "store": deepcopy(job["store"]),
                "budget_fen": recipe["budget_fen"], "missing_slot": job["missing_slot"],
                "candidate_count": 0, "quoted_count": 0,
                "message": "当前门店资料中没有已接受的匹配餐品，本轮未核价。",
                "next_actions": [
                    {"action": "retry_later", "requires": "重新查询菜单与详情，不能保证何时恢复可售"},
                    {"action": "choose_store", "requires": "用户选择其他门店后重新查询"},
                    {"action": "revise_choices", "requires": "用户明确接受新的餐品候选，保留其他硬约束"}]}
    requests = {r["request_fingerprint"]: r for r in job["requests"]}
    by_request = {}
    for receipt in quotes:
        _keys(receipt, ("request", "response", "called_at", "reference"))
        _text(receipt["reference"])
        key = fingerprint(receipt["request"])
        if key not in requests or key in by_request:
            raise RecipeError("报价不属于当前任务，或重复提交")
        called_at = timestamp(receipt["called_at"])
        observed_at = timestamp(job["observed_at"])
        if called_at < observed_at:
            raise RecipeError("报价早于本轮门店资料")
        if job["mode"] == "live":
            current = now or datetime.now(timezone.utc)
            age = (current - called_at).total_seconds()
            binding_age = (current - observed_at).total_seconds()
            if not 0 <= age <= 120 or not 0 <= binding_age <= 600:
                raise RecipeError("报价或门店资料已过本地复核窗口，需重新查询核价")
        response = receipt["response"]
        if not isinstance(response, dict) or response.get("success") is not True or response.get("code") != 200:
            raise RecipeError("服务端未成功核价，不用旧报价补齐")
        data = response.get("data")
        if not isinstance(data, dict):
            raise RecipeError("报价缺少data")
        total = _integer(data.get("price"), 0, 10000000)
        original = _integer(data.get("originalPrice"), 0, 10000000)
        discount = _integer(data.get("discount"), 0, 10000000)
        if original - discount != total:
            raise RecipeError("报价金额不一致")
        echoed = data.get("productList")
        if not isinstance(echoed, list) or not echoed:
            raise RecipeError("缺少报价餐品回显")
        def quantities(items):
            quantities = Counter()
            for item in items:
                _text(item.get("productCode"))
                quantities[item["productCode"]] += _integer(item.get("quantity"), 1, 20)
            return quantities
        if quantities(receipt["request"]["items"]) != quantities(echoed):
            raise RecipeError("报价回显与请求的商品或数量不一致")
        # Detail/request checks do not establish that the response echoed a choice.
        row = requests[key]
        by_request[key] = {"cart": row["cart"], "total_fen": total,
                           "preference_rank": row["preference_rank"], "differences": row["differences"],
                           "option_echo": row["option_echo"], "called_at": receipt["called_at"],
                           "reference": receipt["reference"], "within_budget": total <= job["recipe"]["budget_fen"]}
    result = {"mode": job["mode"], "provenance_authenticated": False,
              "orderable": False, "order_created": False,
              "budget_fen": job["recipe"]["budget_fen"],
              "quoted_count": len(by_request), "candidate_count": len(requests),
              "search_truncated": job.get("search_truncated", False)}
    if len(by_request) != len(requests):
        return {**result, "status": "incomplete", "reason": "missing_quotes"}
    valid = [r for r in by_request.values() if r["within_budget"]]
    if not valid:
        nearest = min(by_request.values(), key=lambda r: r["total_fen"])
        return {**result, "status": "needs_input", "reason": "no_validated_candidate",
                "closest_quoted_candidate": nearest,
                "shortfall_fen": nearest["total_fen"] - result["budget_fen"]}
    selected = min(valid, key=lambda r: (r["preference_rank"], r["total_fen"]))
    return {**result, "status": "quoted", "selected": selected,
            "needs_option_verification": selected["option_echo"] == "unknown"}


def share_recipe(recipe, output):
    """Write a local portable recipe+text card ZIP. Never publish it."""
    recipe = validate_recipe(recipe)
    # Escape card syntax; the JSON itself remains lossless structured data.
    def safe(text):
        import re
        from html import escape
        return re.sub(r"([\\`*_{}\[\]()#+.!|>\-])", r"\\\1", escape(text))
    lines = [f"# {safe(recipe['name'])}", "", f"整单预算：{recipe['budget_fen'] / 100:.2f}元", ""]
    for slot in recipe["slots"]:
        choices = [safe(c["name"]) + ("（" + "、".join(safe(k) + "：" + safe(v) for k, v in c["options"].items()) + "）" if c["options"] else "") for c in slot["choices"]]
        lines.append(f"- {safe(slot['label'])} × {slot['quantity']}：" + " → ".join(choices))
    lines.extend(("", "候选按偏好顺序排列；导入后选择本人门店并重新查询菜单和整单核价。",
                  "本卡不含可用报价、个人券或下单授权。达不到约束时请返回待决定事项。", ""))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("meal.recipe.json", export_recipe(recipe))
        archive.writestr("card.md", "\n".join(lines))
    return output
