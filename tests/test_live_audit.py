from copy import deepcopy
import unittest

from tools.audit_live_validation import audit_report


def sample():
    data = {"format": "mcfuke.live-validation.local.v1", "confirmedConditions": {"budget": {"limitFen": 4000}},
            "stores": [], "callLog": [], "rawResponsePersistence": {"persisted": False}}
    for index in (1, 2):
        code = f"TEST-STORE-{index}"
        price = 3000 + index
        data["stores"].append({"storeCode": code, "completionStatus": "completed", "pricedAt": "test-time",
            "substituted": [], "priceResult": {"priceFen": price, "originalPriceFen": price,
                "discountFen": 0, "priceYuan": str(price / 100), "withinBudget": True,
                "productPriceFen": price, "productListFromResponse": [
                    {"productCode": "TEST-FOOD", "quantity": 1, "subtotalFen": price}]}})
        for tool in ("query-meals", "query-meal-detail", "query-store-coupons", "calculate-price"):
            args = {"storeCode": code, "beType": 1, "orderType": 1}
            output = {}
            if tool == "query-meal-detail":
                args["code"] = "TEST-FOOD"
            if tool == "calculate-price":
                args["items"] = [{"productCode": "TEST-FOOD", "quantity": 1}]
                output = {"priceFen": price, "originalPriceFen": price, "discountFen": 0}
            data["callLog"].append({"step": f"S{len(data['callLog']) + 1}", "tool": tool,
                                    "input": args, "outputCore": output, "status": "success", "localTime": "test-time"})
    return data


class LiveAuditTests(unittest.TestCase):
    def test_consistency_pass_does_not_authenticate_remote_or_replacement(self):
        result = audit_report(sample())
        self.assertTrue(result["consistent"])
        self.assertFalse(result["remote_authenticated"])
        self.assertIn("replacement_branch_not_exercised", result["warnings"])

    def test_store_b_cannot_borrow_store_a_quote(self):
        value = sample()
        value["callLog"][-1]["input"]["storeCode"] = "TEST-STORE-1"
        self.assertIn("store_2:missing_matching_quote", audit_report(value)["errors"])

    def test_echo_quantity_mismatch_is_detected(self):
        value = sample()
        value["callLog"][-1]["input"]["items"][0]["quantity"] = 2
        self.assertIn("store_2:cart_echo_mismatch", audit_report(value)["errors"])

    def test_report_amount_cannot_disagree_with_call(self):
        value = sample()
        value["stores"][0]["priceResult"]["priceFen"] = 1000
        self.assertIn("store_1:missing_matching_quote", audit_report(value)["errors"])

    def test_budget_claim_checked_against_integer_amount(self):
        value = sample()
        value["confirmedConditions"]["budget"]["limitFen"] = 1000
        self.assertIn("store_1:budget_claim_mismatch", audit_report(value)["errors"])

    def test_unknown_tool_or_write_operation_is_flagged(self):
        value = sample()
        value["callLog"][0]["tool"] = "create-order"
        self.assertIn("unexpected_or_writing_tool_in_log", audit_report(value)["errors"])

    def test_own_store_details_required(self):
        value = sample()
        value["callLog"][1]["input"]["code"] = "UNRELATED"
        self.assertIn("store_1:missing_own_store_detail", audit_report(value)["errors"])

    def test_duplicate_steps_and_bool_money_rejected(self):
        value = sample()
        value["callLog"][0]["step"] = value["callLog"][1]["step"]
        value["stores"][0]["priceResult"]["priceFen"] = True
        result = audit_report(value)
        self.assertIn("missing_or_duplicate_step", result["errors"])
        self.assertIn("store_1:invalid_money", result["errors"])

    def test_malformed_payload_does_not_pass(self):
        for value in (None, {}, {"stores": "not-a-list", "callLog": []}):
            self.assertFalse(audit_report(deepcopy(value))["consistent"])


if __name__ == "__main__":
    unittest.main()
