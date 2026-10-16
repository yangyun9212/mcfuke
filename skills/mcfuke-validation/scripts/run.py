"""Self-contained packaged entry; in source checkout, reuse canonical project code."""

import argparse
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
CHECKOUT = SKILL_DIR.parent.parent
# Packaged code always wins, including when Python is started with -I.
sys.path.insert(0, str(SCRIPT_DIR))
if not (SCRIPT_DIR / "mcfuke").is_dir() and (CHECKOUT / "mcfuke").is_dir():
    sys.path.insert(0, str(CHECKOUT))

from mcfuke.recipe import RecipeError, export_recipe, load_recipe  # noqa: E402
from mcfuke.__main__ import SimulatedStore  # noqa: E402
from mcfuke.recipe import execute_recipe  # noqa: E402
from mcfuke.workflow import prepare_job, assess_job, share_recipe  # noqa: E402
from mcfuke.cards import render_result_card  # noqa: E402
import json  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="麦麦复刻：配方、核价衔接与结果卡，脚本不联网")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("validate", help="只校验格式；不认证数据来源")
    check.add_argument("--recipe", type=Path, required=True)
    demo = sub.add_parser("demo", help="纯模拟，不连接官方MCP")
    demo.add_argument("--scenario", choices=("all", "original", "alternative", "overbudget", "unknown"),
                      default="all")
    prepare = sub.add_parser("prepare", help="生成交给宿主MCP执行的核价请求，不联网")
    prepare.add_argument("--recipe", type=Path, required=True)
    prepare.add_argument("--bindings", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    assess = sub.add_parser("assess", help="核对本轮请求与报价回执，不认证官方来源")
    assess.add_argument("--job", type=Path, required=True)
    assess.add_argument("--receipts", type=Path, required=True)
    assess.add_argument("--output", type=Path, required=True)
    card = sub.add_parser("card", help="复核回执并生成本地SVG结果卡，不发布")
    card.add_argument("--job", type=Path, required=True)
    card.add_argument("--receipts", type=Path, required=True)
    card.add_argument("--output", type=Path, required=True)
    card.add_argument("--historical-at", help="历史复核时点ISO8601；显式标记历史回放")
    share = sub.add_parser("share", help="生成不含门店、券或报价的本地分享包")
    share.add_argument("--recipe", type=Path, required=True)
    share.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.command == "validate":
            value = load_recipe(args.recipe)
            print(json.dumps({"format_valid": True, "official_source_verified": False,
                              "recipe": json.loads(export_recipe(value))}, ensure_ascii=False, indent=2))
        elif args.command == "demo":
            sample = SKILL_DIR / "assets" / "lunch.recipe.json"
            if not sample.is_file():
                sample = CHECKOUT / "examples" / "lunch.recipe.json"
            recipe = load_recipe(sample)
            scenarios = ("original", "alternative", "overbudget", "unknown") if args.scenario == "all" else (args.scenario,)
            print(json.dumps({
                "notice": "纯模拟：虚构餐品、门店和价格；不联网、不创建订单。",
                "results": [execute_recipe(recipe, SimulatedStore(s)) for s in scenarios],
            }, ensure_ascii=False, indent=2))
        elif args.command == "share":
            if args.output.suffix != ".zip":
                raise RecipeError("分享包需为ZIP")
            share_recipe(load_recipe(args.recipe), args.output)
            print(json.dumps({"ok": True, "published": False, "order_created": False}))
        else:
            def read_json(path):
                with path.open(encoding="utf-8-sig") as stream:
                    raw = stream.read(1048577)
                if len(raw) > 1048576:
                    raise RecipeError("输入文件过大")
                return json.loads(raw)
            if args.command == "prepare":
                result = prepare_job(load_recipe(args.recipe), read_json(args.bindings))
            elif args.command == "card":
                if args.output.suffix != ".svg":
                    raise RecipeError("结果卡需为SVG")
                content = render_result_card(read_json(args.job), read_json(args.receipts), args.historical_at)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(content, encoding="utf-8")
                print(json.dumps({"ok": True, "published": False, "order_created": False}))
                return 0
            else:
                result = assess_job(read_json(args.job), read_json(args.receipts))
            if args.output.suffix != ".json":
                raise RecipeError("结果需为JSON")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"status": result["status"], "orderable": False}, ensure_ascii=False))
        return 0
    except RecipeError as error:
        print(json.dumps({"ok": False, "reason": "validation_failed", "detail": str(error)}, ensure_ascii=False))
        return 2
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        # Never echo input values, raw parser documents or credential-bearing paths.
        print(json.dumps({"ok": False, "reason": "invalid_or_unreadable_recipe"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
