"""Issue #1 acceptance checks using only fictional menu and quote data."""

from copy import deepcopy
import unittest

from mcfuke.workflow import prepare_job, assess_job
from test_workflow import inputs, receipts


def flexible_inputs():
    recipe, bindings = inputs("simulated")
    recipe["budget_fen"] = 4000
    recipe["slots"][0]["choices"] = recipe["slots"][0]["choices"][:1]
    recipe["slots"][1]["choices"].append(
        {"name": "测试无糖大杯", "options": {"糖": "无糖", "规格": "大杯"}})
    large = deepcopy(bindings["menu"][-1])
    large.update({"sku": "TEST-DRINK-LARGE", "name": "测试无糖大杯",
                  "options": {"糖": "无糖", "规格": "大杯"}})
    large["request_item"]["roundList"][0]["comboItemList"][0]["code"] = "TEST-ZERO-LARGE"
    bindings["menu"].append(large)
    return recipe, bindings


class CupChoiceTests(unittest.TestCase):
    def test_unavailable_preferred_cup_uses_approved_size_without_changing_sugar(self):
        recipe, bindings = flexible_inputs()
        bindings["menu"][2]["available"] = False
        job = prepare_job(recipe, bindings)
        result = assess_job(job, receipts(job, (3400,)))
        self.assertEqual(result["status"], "quoted")
        drink = result["selected"]["cart"][1]
        self.assertEqual(drink["options"], {"糖": "无糖", "规格": "大杯"})
        self.assertEqual(result["selected"]["differences"][0]["slot"], "饮料")
        child = job["requests"][0]["request"]["items"][1]["roundList"][0]["comboItemList"][0]
        self.assertEqual(child["code"], "TEST-ZERO-LARGE")
        self.assertTrue(result["needs_option_verification"])

    def test_cheaper_unapproved_cup_is_not_added_to_requests(self):
        recipe, bindings = flexible_inputs()
        extra = deepcopy(bindings["menu"][-1])
        extra.update({"sku": "TEST-UNAPPROVED", "name": "测试无糖其他杯型",
                      "options": {"糖": "无糖", "规格": "其他杯型"}})
        bindings["menu"].append(extra)
        job = prepare_job(recipe, bindings)
        self.assertEqual(len(job["requests"]), 2)
        result = assess_job(job, receipts(job, (4100, 4200)))
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(result["shortfall_fen"], 100)

    def test_sugared_or_unknown_sugar_alternative_never_satisfies_sugar_free(self):
        for sugar in ("含糖", None):
            with self.subTest(sugar=sugar):
                recipe, bindings = flexible_inputs()
                bindings["menu"][2]["available"] = False
                if sugar is None:
                    bindings["menu"][-1]["options"].pop("糖")
                else:
                    bindings["menu"][-1]["options"]["糖"] = sugar
                job = prepare_job(recipe, bindings)
                self.assertEqual(job["status"], "needs_input")
                self.assertEqual(job["missing_slot"], "饮料")
                self.assertEqual(job["requests"], [])


if __name__ == "__main__":
    unittest.main()
