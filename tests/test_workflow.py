from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
import zipfile

from mcfuke.recipe import RecipeError
from mcfuke.workflow import prepare_job, assess_job, share_recipe


def inputs(mode="recorded"):
    recipe = {"format": "mcfuke.recipe.v1", "name": "分享测试", "budget_fen": 3300,
              "slots": [{"label": "主食", "quantity": 1, "choices": [
                  {"name": "测试主食A", "options": {}}, {"name": "测试主食B", "options": {}}]},
                        {"label": "饮料", "quantity": 1, "choices": [
                            {"name": "测试无糖中杯", "options": {"糖": "无糖", "规格": "中杯"}}]}]}
    bindings = {"format": "mcfuke.bindings.v1", "mode": mode, "observed_at": "2026-10-15T17:00:00+08:00",
                "store": {"code": "TEST-STORE", "name": "虚构测试店"}, "menu": []}
    for sku, name in (("TEST-A", "测试主食A"), ("TEST-B", "测试主食B"), ("TEST-DRINK", "测试无糖中杯")):
        item = {"sku": sku, "name": name, "options": {}, "available": True,
                "detail_reference": "test_detail", "option_echo": "not_applicable",
                "request_item": {"productCode": sku, "quantity": 1}}
        if sku == "TEST-DRINK":
            item.update({"options": {"糖": "无糖", "规格": "中杯"}, "option_echo": "confirmed"})
            item["request_item"]["roundList"] = [{"round": "1", "comboItemList": [
                {"code": "TEST-ZERO-MEDIUM", "quantity": 1, "modification": {"values": [
                    {"code": "TEST-ICE", "key": "0-1", "quantity": 1}]}}]}]
        bindings["menu"].append(item)
    return recipe, bindings


def receipts(job, totals=(3350, 3400)):
    output = {"format": "mcfuke.receipts.v1", "mode": job["mode"], "quotes": []}
    for row, total in zip(job["requests"], totals):
        response = {"success": True, "code": 200, "data": {
            "price": total, "originalPrice": total, "discount": 0,
            "productList": [{"productCode": i["productCode"], "quantity": i["quantity"]}
                            for i in row["request"]["items"]]}}
        output["quotes"].append({"request": deepcopy(row["request"]), "response": response,
                                 "called_at": "2026-10-15T17:00:10+08:00", "reference": "test_call"})
    return output


class WorkflowTests(unittest.TestCase):
    def unavailable_job(self, mode="recorded"):
        recipe, bindings = inputs(mode)
        for item in bindings["menu"][:2]:
            item["available"] = False
        return prepare_job(recipe, bindings)

    def test_unavailable_approved_choices_finish_without_quote_or_silent_replacement(self):
        job = self.unavailable_job()
        result = assess_job(job, receipts(job))
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(result["reason"], "no_approved_match")
        self.assertEqual(result["missing_slot"], "主食")
        self.assertEqual(result["quoted_count"], 0)
        self.assertNotIn("selected", result)
        self.assertFalse(result["orderable"])
        self.assertEqual(len(result["next_actions"]), 3)

    def test_unavailable_job_rejects_quotes_or_changed_conditions(self):
        job = self.unavailable_job()
        data = receipts(job)
        data["quotes"] = receipts(prepare_job(*inputs()))["quotes"][:1]
        with self.assertRaises(RecipeError):
            assess_job(job, data)
        job["recipe"]["budget_fen"] += 100
        with self.assertRaises(RecipeError):
            assess_job(job, receipts(job))

    def test_unavailable_live_observation_expires_and_cannot_be_from_future(self):
        job = self.unavailable_job("live")
        for when in ("2026-10-15T17:11:00+08:00", "2026-10-15T16:59:00+08:00"):
            with self.subTest(when=when), self.assertRaises(RecipeError):
                assess_job(job, receipts(job), now=datetime.fromisoformat(when))
        result = assess_job(job, receipts(job), now=datetime.fromisoformat("2026-10-15T17:01:00+08:00"))
        self.assertEqual(result["status"], "needs_input")

    def test_unavailable_cli_writes_normal_result_with_zero_exit(self):
        job = self.unavailable_job()
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "job.json").write_text(json.dumps(job), encoding="utf-8")
            (directory / "receipts.json").write_text(json.dumps(receipts(job)), encoding="utf-8")
            command = [sys.executable, str(root / "skills/mcfuke-validation/scripts/run.py"),
                       "assess", "--job", str(directory / "job.json"),
                       "--receipts", str(directory / "receipts.json"),
                       "--output", str(directory / "result.json")]
            completed = subprocess.run(command, capture_output=True, encoding="utf-8", cwd=tmp)
            self.assertEqual(completed.returncode, 0, completed.stdout)
            result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["reason"], "no_approved_match")

    def test_nested_option_binding_preserved_without_mutating_source(self):
        recipe, bindings = inputs()
        before = deepcopy(bindings)
        job = prepare_job(recipe, bindings)
        item = job["requests"][0]["request"]["items"][1]
        self.assertEqual(item["productCode"], "TEST-DRINK")
        self.assertEqual(item["roundList"][0]["comboItemList"][0]["code"], "TEST-ZERO-MEDIUM")
        self.assertEqual(bindings, before)

    def test_all_overbudget_is_business_result_with_exact_shortfall(self):
        job = prepare_job(*inputs())
        result = assess_job(job, receipts(job))
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(result["shortfall_fen"], 50)
        self.assertFalse(result["orderable"])

    def test_successful_alternative_has_delta_but_not_unproven_option_confirmation(self):
        job = prepare_job(*inputs())
        result = assess_job(job, receipts(job, totals=(3350, 3200)))
        self.assertEqual(result["status"], "quoted")
        self.assertEqual(result["selected"]["differences"][0]["to"], "测试主食B")
        self.assertTrue(result["needs_option_verification"])
        self.assertFalse(result["provenance_authenticated"])

    def test_other_store_receipt_rejected(self):
        job = prepare_job(*inputs())
        data = receipts(job)
        data["quotes"][0]["request"]["storeCode"] = "OTHER"
        with self.assertRaises(RecipeError):
            assess_job(job, data)

    def test_missing_and_duplicate_quotes_are_not_complete(self):
        job = prepare_job(*inputs())
        data = receipts(job)
        data["quotes"].pop()
        self.assertEqual(assess_job(job, data)["status"], "incomplete")
        data["quotes"].append(deepcopy(data["quotes"][0]))
        with self.assertRaises(RecipeError):
            assess_job(job, data)

    def test_live_expiry_window_is_local_policy_and_enforced(self):
        job = prepare_job(*inputs("live"))
        now = datetime.fromisoformat("2026-10-15T17:03:00+08:00")
        with self.assertRaises(RecipeError):
            assess_job(job, receipts(job), now=now)
        current = datetime.fromisoformat("2026-10-15T17:01:00+08:00")
        self.assertEqual(assess_job(job, receipts(job, totals=(3200, 3250)), now=current)["status"], "quoted")

    def test_old_job_cannot_be_silently_reused_with_changed_budget(self):
        job = prepare_job(*inputs())
        data = receipts(job)
        job["recipe"]["budget_fen"] = 9999
        with self.assertRaises(RecipeError):
            assess_job(job, data)

    def test_response_failure_or_wrong_quantity_rejected(self):
        job = prepare_job(*inputs())
        for mutation in ("failure", "quantity"):
            with self.subTest(mutation=mutation):
                data = receipts(job)
                if mutation == "failure":
                    data["quotes"][0]["response"]["success"] = False
                else:
                    data["quotes"][0]["response"]["data"]["productList"][0]["quantity"] = 2
                with self.assertRaises(RecipeError):
                    assess_job(job, data)

    def test_secret_fields_in_request_item_and_mode_switch_rejected(self):
        recipe, bindings = inputs()
        bindings["menu"][0]["request_item"]["Authorization"] = "private"
        with self.assertRaises(RecipeError):
            prepare_job(recipe, bindings)
        job = prepare_job(*inputs())
        data = receipts(job)
        data["mode"] = "live"
        with self.assertRaises(RecipeError):
            assess_job(job, data)

    def test_share_bundle_contains_only_portable_recipe_and_card(self):
        recipe, _ = inputs()
        recipe["name"] = "<script>测试</script>"
        with tempfile.TemporaryDirectory() as tmp:
            path = share_recipe(recipe, Path(tmp) / "share.zip")
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(set(archive.namelist()), {"meal.recipe.json", "card.md"})
                shared = json.loads(archive.read("meal.recipe.json"))
                self.assertEqual(shared, recipe)
                self.assertNotIn("<script>", archive.read("card.md").decode("utf-8"))
                self.assertNotIn("TEST-STORE", archive.read("card.md").decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
