"""Strict portable recipes and bounded execution against an injected gateway.

The normalized menu contract here is internal, not an official MCP schema.
Prices must come from the gateway's whole-cart quote, never menu arithmetic.
"""

from copy import deepcopy
from hashlib import sha256
from itertools import islice, product
import json
import unicodedata


class RecipeError(ValueError):
    pass


class QuoteRejected(Exception):
    """An explicit business rejection, safe to consider another candidate."""


OPTION_KEYS = frozenset(("糖", "冰量", "温度", "规格", "配料"))


def _keys(value, required, optional=()):
    if not isinstance(value, dict):
        raise RecipeError("需要JSON对象")
    if set(value) - set(required) - set(optional) or set(required) - set(value):
        raise RecipeError("字段缺失或包含配方格式不允许的字段")


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 120:
        raise RecipeError("名称必须为1至120个字符")
    if any(unicodedata.category(c).startswith("C") for c in value):
        raise RecipeError("名称不能包含控制字符")
    return unicodedata.normalize("NFC", value.strip())


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise RecipeError("金额或数量必须为范围内的整数")
    return value


def validate_recipe(value):
    _keys(value, ("format", "name", "budget_fen", "slots"))
    if value["format"] != "mcfuke.recipe.v1":
        raise RecipeError("不支持的配方版本")
    result = {
        "format": value["format"], "name": _text(value["name"]),
        "budget_fen": _integer(value["budget_fen"], 1, 100000), "slots": [],
    }
    slots = value["slots"]
    if not isinstance(slots, list) or not 1 <= len(slots) <= 8:
        raise RecipeError("需要1至8个餐品位置")
    labels = set()
    for slot in slots:
        _keys(slot, ("label", "quantity", "choices"))
        label = _text(slot["label"])
        if label in labels:
            raise RecipeError("餐品位置名称不能重复")
        labels.add(label)
        choices = slot["choices"]
        if not isinstance(choices, list) or not 1 <= len(choices) <= 5:
            raise RecipeError("每个位置需要1至5个已接受的候选")
        clean_choices = []
        for choice in choices:
            _keys(choice, ("name", "options"))
            options = choice["options"]
            if not isinstance(options, dict) or len(options) > 8:
                raise RecipeError("规格需要对象，最多8项")
            if set(options) - OPTION_KEYS:
                raise RecipeError("规格只能包含糖、冰量、温度、规格、配料")
            clean_choices.append({
                "name": _text(choice["name"]),
                "options": {_text(k): _text(v) for k, v in options.items()},
            })
        result["slots"].append({
            "label": label, "quantity": _integer(slot["quantity"], 1, 20),
            "choices": clean_choices,
        })
    return result


def load_recipe(path):
    with open(path, encoding="utf-8") as stream:
        raw = stream.read(32769)
    if len(raw) > 32768:
        raise RecipeError("配方过大")
    return validate_recipe(json.loads(raw))


def export_recipe(recipe):
    """Unknown fields (including credentials or origin account data) are rejected."""
    return json.dumps(validate_recipe(recipe), ensure_ascii=False, indent=2) + "\n"


def fingerprint(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def resolve_recipe(recipe, menu, max_candidates=5):
    """Resolve accepted choices against one menu; do not compute prices."""
    recipe = validate_recipe(recipe)
    _integer(max_candidates, 1, 5)
    if not isinstance(menu, list) or any(not isinstance(item, dict) for item in menu):
        return {"status": "incomplete", "reason": "invalid_menu"}
    pools = []
    for slot in recipe["slots"]:
        matches = []
        for rank, choice in enumerate(slot["choices"]):
            for item in menu:
                if item.get("available") is not True or item.get("name") != choice["name"]:
                    continue
                options = item.get("options")
                if not isinstance(options, dict):
                    continue
                if set(options) - OPTION_KEYS or any(not isinstance(v, str) for v in options.values()):
                    continue
                # Unknown options cannot prove a hard requirement is satisfied.
                if any(options.get(k) != v for k, v in choice["options"].items()):
                    continue
                if not isinstance(item.get("sku"), str) or not item["sku"]:
                    continue
                matches.append({
                    "slot": slot["label"], "name": item["name"], "sku": item["sku"],
                    "quantity": slot["quantity"], "options": deepcopy(options),
                    "preference_rank": rank, "preferred_name": slot["choices"][0]["name"],
                })
        if not matches:
            return {"status": "needs_input", "reason": "no_approved_match",
                    "missing_slot": slot["label"]}
        pools.append(matches)

    total_combinations = 1
    for pool in pools:
        total_combinations *= len(pool)
    candidates = []
    seen = set()
    for combination in islice(product(*pools), max_candidates):
        cart = [{k: item[k] for k in ("slot", "name", "sku", "quantity", "options")}
                for item in combination]
        cart_hash = fingerprint(cart)
        if cart_hash in seen:
            continue
        seen.add(cart_hash)
        candidates.append({"cart": cart, "cart_fingerprint": cart_hash,
                           "preference_rank": sum(i["preference_rank"] for i in combination),
                           "differences": [
                               {"slot": i["slot"], "from": i["preferred_name"], "to": i["name"],
                                "reason": "approved_alternative"}
                               for i in combination if i["preference_rank"] > 0]})
    return {"status": "resolved", "candidates": candidates,
            "search_truncated": total_combinations > max_candidates}


def execute_recipe(recipe, gateway, max_candidates=5):
    """Prepare a result only; never create or authorize an order."""
    recipe = validate_recipe(recipe)
    _integer(max_candidates, 1, 5)
    if gateway.mode not in ("simulated", "recorded", "live"):
        raise RecipeError("未知数据模式")
    result = {
        "recipe_name": recipe["name"], "recipe_fingerprint": fingerprint(recipe),
        "mode": gateway.mode, "store": gateway.store_name,
        "status": "incomplete", "order_created": False,
        "budget_fen": recipe["budget_fen"], "attempts": [], "differences": [],
    }
    try:
        menu = gateway.fetch_menu()
    except Exception:
        return {**result, "reason": "menu_request_failed"}
    resolved = resolve_recipe(recipe, menu, max_candidates)
    if resolved["status"] != "resolved":
        return {**result, **resolved}
    result["search_truncated"] = resolved["search_truncated"]
    valid = []
    over_budget = []
    for candidate in resolved["candidates"]:
        cart = candidate["cart"]
        cart_hash = candidate["cart_fingerprint"]
        attempt = {"cart_fingerprint": cart_hash}
        try:
            quote = gateway.quote(deepcopy(cart))
        except QuoteRejected:
            result["attempts"].append({**attempt, "status": "business_rejected"})
            continue
        except Exception:
            result["attempts"].append({**attempt, "status": "request_failed"})
            # Stop, and do not expose an earlier candidate as a fresh executable result.
            return {**result, "reason": "quote_request_failed"}
        if (not isinstance(quote, dict) or type(quote.get("total_fen")) is not int
                or quote["total_fen"] < 0
                or not isinstance(quote.get("reference"), str) or not quote["reference"]):
            result["attempts"].append({**attempt, "status": "invalid_quote"})
            return {**result, "reason": "invalid_quote"}
        quote = {"total_fen": quote["total_fen"], "reference": quote["reference"]}
        within = quote["total_fen"] <= recipe["budget_fen"]
        result["attempts"].append({**attempt, "status": "within_budget" if within else "over_budget",
                                  "total_fen": quote["total_fen"]})
        if within:
            valid.append((candidate["preference_rank"], quote["total_fen"],
                          cart, deepcopy(quote), candidate["differences"]))
        else:
            over_budget.append({"cart": cart, "total_fen": quote["total_fen"],
                                "shortfall_fen": quote["total_fen"] - recipe["budget_fen"],
                                "within_budget": False})
    if not valid:
        result.update({"status": "needs_input", "reason": "no_validated_candidate", "orderable": False})
        if over_budget:
            result["closest_quoted_candidate"] = min(over_budget, key=lambda v: v["total_fen"])
        return result
    # Honor the user's accepted preference order first; price breaks a tie.
    _, _, cart, quote, differences = min(valid, key=lambda value: value[:2])
    result.update({"status": "prepared", "cart": cart, "quote": quote,
                   "plan_fingerprint": fingerprint({"store": gateway.store_name,
                                                    "cart": cart, "quote": quote})})
    result["differences"] = differences
    result["orderable"] = False  # Live freshness and confirmation adapter not implemented.
    return result
