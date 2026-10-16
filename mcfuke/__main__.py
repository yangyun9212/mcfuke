"""Offline feasibility demo. Every menu and price here is invented test data."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

from .recipe import load_recipe, execute_recipe


class SimulatedStore:
    mode = "simulated"

    def __init__(self, scenario):
        self.scenario = scenario
        self.store_name = "虚构测试店A" if scenario == "original" else "虚构测试店B"
        self.calls = 0

    def fetch_menu(self):
        menu = [
            {"sku": "TEST-CHICKEN-A", "name": "测试鸡肉堡A", "options": {}, "available": True},
            {"sku": "TEST-CHICKEN-B", "name": "测试鸡肉堡B", "options": {}, "available": True},
            {"sku": "TEST-DRINK", "name": "测试饮料", "options": {"糖": "无糖"}, "available": True},
        ]
        if self.scenario != "original":
            menu[0]["available"] = False
        if self.scenario == "unknown":
            menu[2]["options"] = {}
        return deepcopy(menu)

    def quote(self, cart):
        self.calls += 1
        # Deliberately whole-cart values, not derived by adding menu display prices.
        if self.scenario == "overbudget":
            total = 3500
        elif any(item["sku"] == "TEST-CHICKEN-A" for item in cart):
            total = 2600
        else:
            total = 2800
        return {"total_fen": total, "reference": f"SIMULATED-{self.scenario}-{self.calls}"}


def main():
    parser = argparse.ArgumentParser(description="麦麦复刻：纯模拟配方验证，不连接MCP、不下单")
    parser.add_argument("--recipe", type=Path,
                        default=Path(__file__).resolve().parents[1] / "examples" / "lunch.recipe.json")
    parser.add_argument("--scenario", choices=("all", "original", "alternative", "overbudget", "unknown"),
                        default="all")
    args = parser.parse_args()
    recipe = load_recipe(args.recipe)
    scenarios = ("original", "alternative", "overbudget", "unknown") if args.scenario == "all" else (args.scenario,)
    output = {
        "notice": "纯模拟：餐品、门店、价格均为虚构测试数据；未连接官方MCP，未创建订单。",
        "results": [execute_recipe(recipe, SimulatedStore(scenario)) for scenario in scenarios],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
