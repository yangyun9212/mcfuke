from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from mcfuke.__main__ import SimulatedStore
from mcfuke.recipe import RecipeError, QuoteRejected, execute_recipe, export_recipe, load_recipe


class RecipeTests(unittest.TestCase):
    def setUp(self):
        self.recipe = load_recipe(Path(__file__).resolve().parents[1] / "examples" / "lunch.recipe.json")

    def test_export_import_portable_and_roundtrips_without_account_or_sku(self):
        encoded = export_recipe(self.recipe)
        self.assertNotIn("sku", encoded)
        self.assertNotIn("Token", encoded)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recipe.json"
            path.write_text(encoded, encoding="utf-8")
            self.assertEqual(self.recipe, load_recipe(path))

    def test_account_and_executable_fields_are_rejected_at_every_level(self):
        for location, field in (("root", "token"), ("root", "command"), ("slot", "couponId"),
                                ("choice", "sku"), ("choice", "url")):
            with self.subTest(location=location, field=field):
                value = deepcopy(self.recipe)
                target = value if location == "root" else value["slots"][0]
                if location == "choice":
                    target = target["choices"][0]
                target[field] = "unexpected"
                with self.assertRaises(RecipeError):
                    export_recipe(value)

    def test_money_must_be_integer_fen_not_bool_float_or_negative(self):
        for value in (True, 30.0, "3000", -1, 0):
            with self.subTest(value=value):
                recipe = deepcopy(self.recipe)
                recipe["budget_fen"] = value
                with self.assertRaises(RecipeError):
                    export_recipe(recipe)

    def test_duplicate_slot_rejected(self):
        self.recipe["slots"].append(deepcopy(self.recipe["slots"][0]))
        with self.assertRaises(RecipeError):
            export_recipe(self.recipe)

    def test_account_data_cannot_hide_in_options(self):
        self.recipe["slots"][0]["choices"][0]["options"]["token"] = "private"
        with self.assertRaises(RecipeError):
            export_recipe(self.recipe)

    def test_gateway_metadata_is_not_exported_in_result(self):
        class WithMetadata(SimulatedStore):
            def quote(self, cart):
                result = super().quote(cart)
                result["authorization"] = "PRIVATE-METADATA"
                return result
        result = execute_recipe(self.recipe, WithMetadata("original"))
        self.assertEqual(result["status"], "prepared")
        self.assertNotIn("PRIVATE-METADATA", json.dumps(result))

    def test_same_recipe_resolves_different_store_without_mutation(self):
        original = deepcopy(self.recipe)
        a = execute_recipe(self.recipe, SimulatedStore("original"))
        b = execute_recipe(self.recipe, SimulatedStore("alternative"))
        self.assertEqual(a["status"], "prepared")
        self.assertEqual(a["quote"]["total_fen"], 2600)
        self.assertEqual(b["quote"]["total_fen"], 2800)
        self.assertEqual(b["differences"][0]["to"], "测试鸡肉堡B")
        self.assertNotEqual(a["plan_fingerprint"], b["plan_fingerprint"])
        self.assertEqual(self.recipe, original)

    def test_unknown_required_option_stops_without_quote(self):
        store = SimulatedStore("unknown")
        result = execute_recipe(self.recipe, store)
        self.assertEqual(result["missing_slot"], "饮料")
        self.assertEqual(store.calls, 0)

    def test_unapproved_substitute_cannot_be_used(self):
        self.recipe["slots"][0]["choices"] = self.recipe["slots"][0]["choices"][:1]
        result = execute_recipe(self.recipe, SimulatedStore("alternative"))
        self.assertEqual(result["reason"], "no_approved_match")

    def test_overbudget_never_becomes_prepared(self):
        result = execute_recipe(self.recipe, SimulatedStore("overbudget"))
        self.assertEqual(result["status"], "needs_input")
        self.assertNotIn("cart", result)

    def test_business_rejection_allows_next_approved_candidate(self):
        class RejectFirst(SimulatedStore):
            def quote(self, cart):
                if any(i["sku"] == "TEST-CHICKEN-A" for i in cart):
                    raise QuoteRejected()
                return super().quote(cart)
        result = execute_recipe(self.recipe, RejectFirst("original"))
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["attempts"][0]["status"], "business_rejected")

    def test_transport_error_stops_and_does_not_expose_secret_or_old_quote(self):
        class FailSecond(SimulatedStore):
            def quote(self, cart):
                if self.calls:
                    raise RuntimeError("Authorization: Bearer SHOULD-NOT-LEAK")
                return super().quote(cart)
        result = execute_recipe(self.recipe, FailSecond("original"))
        self.assertEqual(result["status"], "incomplete")
        self.assertNotIn("quote", result)
        self.assertNotIn("SHOULD-NOT-LEAK", json.dumps(result))

    def test_malformed_quote_is_rejected(self):
        for value in (-1, True, 26.0, "2600"):
            with self.subTest(value=value):
                class BadPrice(SimulatedStore):
                    def quote(self, cart):
                        return {"total_fen": value, "reference": "test"}
                result = execute_recipe(self.recipe, BadPrice("original"))
                self.assertEqual(result["reason"], "invalid_quote")

    def test_candidate_budget_is_enforced_and_no_global_optimality_claim(self):
        result = execute_recipe(self.recipe, SimulatedStore("original"), max_candidates=1)
        self.assertEqual(len(result["attempts"]), 1)
        self.assertTrue(result["search_truncated"])

    def test_new_execution_fetches_fresh_quote(self):
        store = SimulatedStore("original")
        first = execute_recipe(self.recipe, store)
        store.scenario = "overbudget"
        second = execute_recipe(self.recipe, store)
        self.assertEqual(first["status"], "prepared")
        self.assertEqual(second["status"], "needs_input")

    def test_simulated_result_cannot_create_or_authorize_order(self):
        result = execute_recipe(self.recipe, SimulatedStore("original"))
        self.assertEqual(result["mode"], "simulated")
        self.assertFalse(result["order_created"])
        self.assertFalse(result["orderable"])


if __name__ == "__main__":
    unittest.main()
