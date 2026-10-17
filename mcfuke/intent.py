"""Explicit user conditions compiled to existing recipe.v1 accepted choices.

Local review fingerprints detect changes; they do not authenticate the user,
the official service, or an order authorization.
"""

from copy import deepcopy
from html import escape
import json
from pathlib import Path
import re
import zipfile

from .recipe import OPTION_KEYS, RecipeError, _keys, _text, fingerprint, validate_recipe


SCOPES = {"fixed": "不替换", "size_only": "仅换同款杯型", "accepted_items": "仅换已列出的餐品"}


def options(value):
    if not isinstance(value, dict) or set(value) - OPTION_KEYS:
        raise RecipeError("必须条件与候选规格只能使用已支持的属性")
    return {_text(k): _text(v) for k, v in value.items()}


def validate_intent(value):
    _keys(value, ("format", "name", "budget_fen", "preference_policy", "slots"))
    if value["format"] != "mcfuke.intent.v1" or value["preference_policy"] != "balanced":
        raise RecipeError("条件版本或偏好策略不支持")
    slots = value["slots"]
    if not isinstance(slots, list) or not 1 <= len(slots) <= 8:
        raise RecipeError("需要1至8个餐品位置")
    clean = {k: deepcopy(value[k]) for k in ("format", "name", "budget_fen", "preference_policy")}
    clean["slots"] = []
    recipe_slots = []
    for slot in slots:
        _keys(slot, ("label", "quantity", "required_options", "substitution", "choices"))
        required = options(slot["required_options"])
        scope = slot["substitution"]
        if not isinstance(scope, str) or scope not in SCOPES:
            raise RecipeError("需要明确固定、仅换杯型或已接受餐品范围")
        choices = slot["choices"]
        if not isinstance(choices, list) or not 1 <= len(choices) <= 5:
            raise RecipeError("需要1至5个明确接受的候选，不能用不限代替")
        if scope == "fixed" and len(choices) != 1:
            raise RecipeError("不替换与多个候选冲突")
        cleaned_choices, recipe_choices, seen = [], [], set()
        for choice in choices:
            _keys(choice, ("name", "item_group", "options"))
            chosen_options = options(choice["options"])
            if any(k in chosen_options and chosen_options[k] != v for k, v in required.items()):
                raise RecipeError("候选规格与必须条件冲突，需要用户决定")
            merged = {**chosen_options, **required}
            name = _text(choice["name"])
            group = _text(choice["item_group"])
            key = fingerprint({"name": name, "options": merged})
            if key in seen:
                raise RecipeError("候选重复，偏好顺序不明确")
            seen.add(key)
            cleaned_choices.append({"name": name, "item_group": group, "options": chosen_options})
            recipe_choices.append({"name": name, "options": merged})
        if scope == "size_only":
            groups = {c["item_group"] for c in cleaned_choices}
            remaining = {fingerprint({k: v for k, v in c["options"].items() if k != "规格"})
                         for c in recipe_choices}
            if (len(groups) != 1 or len(remaining) != 1
                    or any("规格" not in c["options"] for c in recipe_choices)):
                raise RecipeError("仅换杯型不能改变饮料或其他规格，且每个杯型需明确")
        clean["slots"].append({"label": _text(slot["label"]), "quantity": slot["quantity"],
                               "required_options": required, "substitution": scope,
                               "choices": cleaned_choices})
        recipe_slots.append({"label": slot["label"], "quantity": slot["quantity"], "choices": recipe_choices})
    recipe = validate_recipe({"format": "mcfuke.recipe.v1", "name": value["name"],
                              "budget_fen": value["budget_fen"], "slots": recipe_slots})
    clean["name"], clean["budget_fen"] = recipe["name"], recipe["budget_fen"]
    return clean


def compile_intent(value):
    intent = validate_intent(value)
    return validate_recipe({"format": "mcfuke.recipe.v1", "name": intent["name"],
                            "budget_fen": intent["budget_fen"], "slots": [
        {"label": s["label"], "quantity": s["quantity"], "choices": [
            {"name": c["name"], "options": {**c["options"], **s["required_options"]}}
            for c in s["choices"]]} for s in intent["slots"]]})


def review_intent(value):
    intent = validate_intent(value)
    return {"format": "mcfuke.intent-review.v1", "intent_fingerprint": fingerprint(intent),
            "recipe": compile_intent(intent), "must_keep": {
                "budget_fen": intent["budget_fen"], "slots": [
                    {"label": s["label"], "quantity": s["quantity"], "options": s["required_options"]}
                    for s in intent["slots"]]},
            "preferences": {"policy": "balanced", "description": "尽量保留各餐品的优先选择，同等偏好选低价",
                            "slots": [{"label": s["label"], "ordered_choices": [c["name"] for c in s["choices"]]}
                                      for s in intent["slots"]]},
            "allowed_changes": [{"label": s["label"], "scope": s["substitution"],
                                 "description": SCOPES[s["substitution"]],
                                 "choices": deepcopy(s["choices"])} for s in intent["slots"]],
            "confirmed": False, "orderable": False}


def confirm_intent(value, reviewed_fingerprint):
    review = review_intent(value)
    if reviewed_fingerprint != review["intent_fingerprint"]:
        raise RecipeError("条件已改变，需重新展示并由用户明确确认")
    return {"format": "mcfuke.intent-confirmation.v1", "accepted": True,
            "intent_fingerprint": review["intent_fingerprint"],
            "recipe_fingerprint": fingerprint(review["recipe"])}


def checked_confirmation(value, confirmation):
    intent = validate_intent(value)
    _keys(confirmation, ("format", "accepted", "intent_fingerprint", "recipe_fingerprint"))
    if (confirmation["format"] != "mcfuke.intent-confirmation.v1" or confirmation["accepted"] is not True
            or confirmation != confirm_intent(intent, confirmation["intent_fingerprint"])):
        raise RecipeError("条件尚未明确确认或确认已失效")
    return intent


def prepare_intent_job(value, confirmation, bindings):
    from .workflow import prepare_job
    intent = checked_confirmation(value, confirmation)
    recipe = compile_intent(intent)
    job = prepare_job(recipe, bindings)
    for slot, compiled in zip(intent["slots"], recipe["slots"]):
        if slot["substitution"] != "size_only":
            continue
        product_codes = {row["request_item"]["productCode"] for row in bindings["menu"]
                         if row["available"] and any(row["name"] == c["name"] and
                             all(row["options"].get(k) == v for k, v in c["options"].items())
                             for c in compiled["choices"])}
        if len(product_codes) > 1:
            raise RecipeError("当前绑定无法确认是同款容器，仅换杯型需重新核实或明确允许换餐品")
    job["intent_fingerprint"] = fingerprint(intent)
    job["intent_summary"] = review_intent(intent)
    job["job_fingerprint"] = fingerprint({k: v for k, v in job.items() if k != "job_fingerprint"})
    return job


def check_job_conditions(job, value, confirmation):
    if "intent_fingerprint" not in job:
        if value is not None or confirmation is not None:
            raise RecipeError("旧任务没有条件绑定，请用确认后的条件重新prepare")
        return
    if value is None or confirmation is None:
        raise RecipeError("条件任务需提供当前intent与confirmation，不能跳过变更检查")
    intent = checked_confirmation(value, confirmation)
    if (fingerprint(intent) != job["intent_fingerprint"] or compile_intent(intent) != job["recipe"]
            or review_intent(intent) != job.get("intent_summary")):
        raise RecipeError("当前条件与核价任务不同，需重新确认并核价")


def share_intent(value, output):
    intent = validate_intent(value)
    # No confirmation receipt crosses users.
    recipe = compile_intent(intent)
    from .recipe import export_recipe
    def safe(text):
        return re.sub(r"([\\`*_{}\[\]()#+.!|>\-])", r"\\\1", escape(str(text)))
    review = review_intent(intent)
    lines = ["# " + safe(intent["name"]), "", "## 必须保留", "",
             f"预算不超过 {intent['budget_fen'] / 100:.2f} 元"]
    for slot in intent["slots"]:
        required = "、".join(safe(k) + "：" + safe(v) for k, v in slot["required_options"].items()) or "按已列候选"
        lines.append(f"- {safe(slot['label'])} × {slot['quantity']}：{required}")
    lines += ["", "## 优先偏好与允许替代", ""]
    for slot in intent["slots"]:
        lines.append(f"- {safe(slot['label'])}：" + " → ".join(safe(c["name"]) for c in slot["choices"])
                     + "；" + safe(SCOPES[slot["substitution"]]))
    lines += ["", safe(review["preferences"]["description"]),
              "", "导入后由接收者重新确认条件、选择本人门店、查询详情并整单核价。",
              "不包含有效报价、个人券、确认回执或下单授权。", ""]
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("meal.intent.json", json.dumps(intent, ensure_ascii=False, indent=2) + "\n")
        archive.writestr("meal.recipe.json", export_recipe(recipe))
        archive.writestr("card.md", "\n".join(lines))
    return output
