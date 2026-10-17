from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from mcfuke.cards import render_result_card
from mcfuke.intent import (compile_intent, confirm_intent, prepare_intent_job, review_intent,
                           share_intent, validate_intent)
from mcfuke.recipe import RecipeError, resolve_recipe, validate_recipe
from mcfuke.workflow import assess_job
from test_cup_choices import flexible_inputs
from test_workflow import receipts


def inputs():
    recipe, bindings = flexible_inputs()
    intent = {"format": "mcfuke.intent.v1", "name": "测试明确条件", "budget_fen": 4000,
              "preference_policy": "balanced", "slots": []}
    for slot in recipe["slots"]:
        drink = slot["label"] == "饮料"
        intent["slots"].append({"label": slot["label"], "quantity": slot["quantity"],
            "required_options": {"糖": "无糖"} if drink else {},
            "substitution": "size_only" if drink else "fixed", "choices": [
                {"name": c["name"], "item_group": "测试同款可乐" if drink else "测试主食",
                 "options": {k: v for k, v in c["options"].items() if k != "糖"}} for c in slot["choices"]]})
    return intent, bindings


def accepted(intent):
    return confirm_intent(intent, review_intent(intent)["intent_fingerprint"])


class IntentTests(unittest.TestCase):
    def test_compile_repeats_hard_requirement_and_preserves_preference_order(self):
        intent, _ = inputs()
        before = deepcopy(intent)
        review = review_intent(intent)
        recipe = review["recipe"]
        self.assertEqual(recipe["format"], "mcfuke.recipe.v1")
        self.assertTrue(all(c["options"]["糖"] == "无糖" for c in recipe["slots"][1]["choices"]))
        self.assertEqual(review["preferences"]["slots"][1]["ordered_choices"], ["测试无糖中杯", "测试无糖大杯"])
        self.assertFalse(review["confirmed"])
        self.assertEqual(intent, before)

    def test_fixed_or_sugar_conflicts_are_rejected_instead_of_relaxed(self):
        for mutation in ("fixed", "sugar", "duplicate", "extra", "quantity"):
            with self.subTest(mutation=mutation):
                intent, _ = inputs()
                slot = intent["slots"][1]
                if mutation == "fixed": slot["substitution"] = "fixed"
                elif mutation == "sugar": slot["choices"][1]["options"]["糖"] = "含糖"
                elif mutation == "duplicate": slot["choices"][1] = deepcopy(slot["choices"][0])
                elif mutation == "extra": intent["token"] = "TEST-NOT-ALLOWED"
                else: slot["quantity"] = True
                with self.assertRaises(RecipeError): validate_intent(intent)

    def test_size_only_cannot_change_drink_or_other_options(self):
        for mutation in ("group", "ice", "size"):
            with self.subTest(mutation=mutation):
                intent, _ = inputs()
                choice = intent["slots"][1]["choices"][1]
                if mutation == "group": choice["item_group"] = "测试另一种饮料"
                elif mutation == "ice": choice["options"]["冰量"] = "少冰"
                else: choice["options"].pop("规格")
                with self.assertRaises(RecipeError): validate_intent(intent)

    def test_changing_drink_requires_explicit_accepted_items_scope(self):
        intent, _ = inputs()
        intent["slots"][1]["substitution"] = "accepted_items"
        intent["slots"][1]["choices"][1]["item_group"] = "测试另一种饮料"
        self.assertEqual(compile_intent(intent)["slots"][1]["choices"][1]["options"]["糖"], "无糖")

    def test_old_review_cannot_confirm_changed_conditions(self):
        intent, _ = inputs()
        old = review_intent(intent)
        intent["budget_fen"] += 100
        with self.assertRaises(RecipeError): confirm_intent(intent, old["intent_fingerprint"])

    def test_prepare_needs_current_confirmation_and_preserves_actual_cup_selection(self):
        intent, bindings = inputs()
        with self.assertRaises(RecipeError): prepare_intent_job(intent, {}, bindings)
        job = prepare_intent_job(intent, accepted(intent), bindings)
        self.assertEqual(len(job["requests"]), 2)
        drink = job["requests"][1]["request"]["items"][1]
        self.assertEqual(drink["productCode"], "TEST-DRINK")
        self.assertEqual(drink["roundList"][0]["comboItemList"][0]["code"], "TEST-ZERO-LARGE")

    def test_same_family_label_does_not_prove_different_product_codes_same_container(self):
        intent, bindings = inputs()
        bindings["menu"][-1]["request_item"]["productCode"] = "TEST-OTHER-PRODUCT"
        with self.assertRaises(RecipeError): prepare_intent_job(intent, accepted(intent), bindings)

    def test_assess_and_card_cannot_omit_current_conditions(self):
        intent, bindings = inputs()
        confirmation = accepted(intent)
        job = prepare_intent_job(intent, confirmation, bindings)
        data = receipts(job, (4100, 3900))
        with self.assertRaises(RecipeError): assess_job(job, data)
        with self.assertRaises(RecipeError): render_result_card(job, data)
        result = assess_job(job, data, current_intent=intent, confirmation=confirmation)
        self.assertEqual(result["selected"]["cart"][1]["options"]["规格"], "大杯")
        self.assertTrue(result["needs_option_verification"])
        svg = render_result_card(job, data, current_intent=intent, confirmation=confirmation)
        for visible in ("必须保留", "糖：无糖", "仅换同款杯型", "测试无糖大杯", "所选规格还需确认"):
            self.assertIn(visible, svg)

    def test_accepted_condition_change_invalidates_old_job_even_with_new_confirmation(self):
        for change in ("budget", "quantity", "scope", "preference", "required"):
            with self.subTest(change=change):
                intent, bindings = inputs()
                job = prepare_intent_job(intent, accepted(intent), bindings)
                data = receipts(job)
                updated = deepcopy(intent)
                if change == "budget": updated["budget_fen"] += 100
                elif change == "quantity": updated["slots"][1]["quantity"] = 2
                elif change == "scope": updated["slots"][1]["substitution"] = "accepted_items"
                elif change == "preference": updated["slots"][1]["choices"].reverse()
                else: updated["slots"][1]["required_options"]["温度"] = "冷"
                with self.assertRaises(RecipeError):
                    assess_job(job, data, current_intent=updated, confirmation=accepted(updated))

    def test_unknown_hard_property_is_not_satisfied_by_cheaper_option(self):
        intent, bindings = inputs()
        bindings["menu"][2]["available"] = False
        bindings["menu"][-1]["options"].pop("糖")
        job = prepare_intent_job(intent, accepted(intent), bindings)
        self.assertEqual(job["status"], "needs_input")
        self.assertEqual(job["requests"], [])
        self.assertEqual(assess_job(job, receipts(job), current_intent=intent,
                                   confirmation=accepted(intent))["quoted_count"], 0)

    def test_share_roundtrip_keeps_conditions_but_never_sender_confirmation(self):
        intent, _ = inputs()
        with tempfile.TemporaryDirectory() as temporary:
            output = share_intent(intent, Path(temporary) / "meal.zip")
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), {"meal.intent.json", "meal.recipe.json", "card.md"})
                imported = json.loads(archive.read("meal.intent.json"))
                recipe = json.loads(archive.read("meal.recipe.json"))
                self.assertEqual(compile_intent(imported), recipe)
                self.assertEqual(imported, validate_intent(intent))
                self.assertNotIn("confirmation", archive.read("meal.intent.json").decode("utf-8"))
                self.assertIn("必须保留", archive.read("card.md").decode("utf-8"))
                self.assertFalse(review_intent(imported)["confirmed"])

    def test_legacy_recipe_parser_does_not_silently_accept_new_intent(self):
        intent, _ = inputs()
        with self.assertRaises(RecipeError): validate_recipe(intent)

    def test_candidate_limit_keeps_better_main_preference_before_distant_drink_choices(self):
        recipe = {"format": "mcfuke.recipe.v1", "name": "测试组合排序", "budget_fen": 4000,
                  "slots": [{"label": label, "quantity": 1, "choices": [
                      {"name": f"{label}{i}", "options": {}} for i in range(count)]}
                      for label, count in (("主食", 2), ("饮料", 5))]}
        menu = [{"sku": c["name"], "name": c["name"], "options": {}, "available": True}
                for slot in recipe["slots"] for c in slot["choices"]]
        result = resolve_recipe(recipe, menu)
        self.assertTrue(any(c["cart"][0]["name"] == "主食1" and c["cart"][1]["name"] == "饮料0"
                            for c in result["candidates"]))
        self.assertEqual([c["preference_rank"] for c in result["candidates"]], [0, 1, 1, 2, 2])
        self.assertTrue(result["search_truncated"])

    def test_cli_condition_flow_and_missing_context_failure(self):
        intent, bindings = inputs()
        cli = [sys.executable, "-I", str(Path(__file__).resolve().parents[1] / "skills/mcfuke-validation/scripts/run.py")]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            def write(name, data):
                (directory / name).write_text(json.dumps(data), encoding="utf-8")
            def run(*args, expected=0):
                result = subprocess.run(cli + list(args), cwd=temporary, capture_output=True, encoding="utf-8", timeout=20)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
            write("intent.json", intent)
            write("bindings.json", bindings)
            run("review", "--intent", "intent.json", "--output", "review.json")
            review = json.loads((directory / "review.json").read_text(encoding="utf-8"))
            run("confirm", "--intent", "intent.json", "--reviewed-fingerprint", review["intent_fingerprint"],
                "--output", "confirmation.json")
            run("prepare", "--intent", "intent.json", "--confirmation", "confirmation.json", "--bindings", "bindings.json",
                "--output", "job.json")
            job = json.loads((directory / "job.json").read_text(encoding="utf-8"))
            write("receipts.json", receipts(job, (4100, 3900)))
            run("assess", "--job", "job.json", "--receipts", "receipts.json", "--output", "bad.json", expected=2)
            self.assertFalse((directory / "bad.json").exists())
            run("assess", "--job", "job.json", "--receipts", "receipts.json", "--intent", "intent.json",
                "--confirmation", "confirmation.json", "--output", "result.json")
            run("card", "--job", "job.json", "--receipts", "receipts.json", "--intent", "intent.json",
                "--confirmation", "confirmation.json", "--output", "result.svg")
            run("share", "--intent", "intent.json", "--output", "share.zip")
            self.assertEqual(json.loads((directory / "result.json").read_text(encoding="utf-8"))["status"], "quoted")


if __name__ == "__main__":
    unittest.main()
