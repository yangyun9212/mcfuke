from datetime import datetime
import unittest
import xml.etree.ElementTree as ET

from mcfuke.cards import render_result_card
from mcfuke.recipe import RecipeError
from mcfuke.workflow import prepare_job
from test_workflow import inputs, receipts


class ResultCardTests(unittest.TestCase):
    def test_old_live_price_requires_explicit_historical_badge(self):
        job = prepare_job(*inputs("live"))
        now = datetime.fromisoformat("2026-10-16T12:00:00+08:00")
        with self.assertRaises(RecipeError):
            render_result_card(job, receipts(job), now=now)
        svg = render_result_card(job, receipts(job), "2026-10-15T17:01:00+08:00", now=now)
        self.assertIn("历史记录回放", svg)
        self.assertIn("报价需重查", svg)
        with self.assertRaises(RecipeError):
            render_result_card(job, receipts(job), "2026-10-17T17:01:00+08:00", now=now)

    def test_overbudget_keeps_shortfall_and_unknown_options_without_private_ids(self):
        job = prepare_job(*inputs())
        svg = render_result_card(job, receipts(job))
        for expected in ("33.50", "34.00", "已接受替代", "0.50", "核价结果未显示具体选配", "未创建订单"):
            self.assertIn(expected, svg)
        for private in ("TEST-STORE", "TEST-DRINK", "test_call", "test_detail", "request_fingerprint"):
            self.assertNotIn(private, svg)

    def test_unavailable_has_no_price_or_false_ready_status(self):
        recipe, bindings = inputs()
        for item in bindings["menu"][:2]:
            item["available"] = False
        job = prepare_job(recipe, bindings)
        svg = render_result_card(job, receipts(job))
        self.assertIn("本轮未核价", svg)
        self.assertNotIn("最低已核价候选", svg)

    def test_missing_receipt_never_presents_partial_quote_as_complete(self):
        job = prepare_job(*inputs())
        data = receipts(job)
        data["quotes"].pop()
        svg = render_result_card(job, data)
        self.assertIn("核价尚未完成", svg)
        self.assertNotIn("33.50", svg)

    def test_foreign_receipt_cannot_be_prettified_into_result(self):
        job = prepare_job(*inputs())
        data = receipts(job)
        data["quotes"][0]["request"]["storeCode"] = "OTHER"
        with self.assertRaises(RecipeError):
            render_result_card(job, data)

    def test_imported_text_is_svg_text_not_executable_markup(self):
        recipe, bindings = inputs()
        recipe["name"] = '<script>alert("test")</script>'
        job = prepare_job(recipe, bindings)
        svg = render_result_card(job, receipts(job, (3200, 3000)))
        root = ET.fromstring(svg)
        allowed = {"svg", "title", "desc", "rect", "g", "text"}
        self.assertTrue(all(node.tag.split("}")[-1] in allowed for node in root.iter()))
        self.assertIn("已核价，仍需确认", svg)
        self.assertIn("历史记录回放", svg)
        self.assertIn("已接受替代 · 最低报价", svg)
        self.assertNotIn("首选餐单 · 最低报价", svg)


if __name__ == "__main__":
    unittest.main()
