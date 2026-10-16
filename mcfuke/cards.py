"""Local, self-contained SVG results; no network, scripts or account identifiers."""

from datetime import datetime, timezone
from html import escape
import unicodedata

from .recipe import RecipeError, fingerprint, validate_recipe
from .workflow import assess_job, timestamp


def money(fen):
    return f"{fen // 100}.{fen % 100:02d}"


def wrap(value, width=52):
    lines, line, size = [], "", 0
    for char in value:
        weight = 2 if unicodedata.east_asian_width(char) in "WF" or char in "MW@%" else 1.5
        if size + weight > width:
            lines.append(line)
            line, size = "", 0
        line += char
        size += weight
    return lines + [line] if line else lines


def render_result_card(job, receipts, historical_at=None, now=None):
    """Reassess evidence before rendering. Historical time always changes the badge."""
    current = now or datetime.now(timezone.utc)
    evaluation = timestamp(historical_at) if historical_at else current
    if evaluation > current:
        raise RecipeError("历史复核时间不能晚于当前时间")
    result = assess_job(job, receipts, now=evaluation)
    recipe = validate_recipe(job["recipe"])
    historical = historical_at is not None or result["mode"] == "recorded"
    badge = ("纯模拟 · 虚构数据" if result["mode"] == "simulated" else
             "历史记录回放 · 报价需重查" if historical else "本轮核价记录 · 执行前需复核")
    titles = {"quoted": "已核价，仍需确认", "needs_input": "需要你决定下一步", "incomplete": "核价尚未完成"}
    parts, y = [], 0

    def rect(x, top, width, height, fill, radius=0, stroke=None):
        border = f' stroke="{stroke}"' if stroke else ""
        parts.append(f'<rect x="{x}" y="{top}" width="{width}" height="{height}" rx="{radius}" fill="{fill}"{border}/>')

    def text(value, x, top, size=20, color="#20242a", bold=False, end=False):
        parts.append(f'<text x="{x}" y="{top}" font-size="{size}" fill="{color}"'
                     f' text-anchor="{"end" if end else "start"}"'
                     f' font-weight="{700 if bold else 400}">{escape(str(value))}</text>')

    def paragraph(value, size=20, color="#66707d", x=52, width=620, bold=False):
        nonlocal y
        for line in wrap(value, 2 * width // size):
            text(line, x, y, size, color, bold)
            y += size + 10

    # This surface is a static card displayed in the conversation, not a checkout.
    text("麦麦复刻", 32, 58, 25, bold=True)
    text("把喜欢的一顿，在这里再吃一次", 32, 85, 14)
    mode = "纯模拟" if result["mode"] == "simulated" else "历史记录回放" if historical else "本轮核价"
    rect(548, 34, 180, 38, "#e8edf1", 19)
    text(mode, 638, 59, 15, "#4e5968", end=False)
    # Center the short mode label using SVG's text anchor.
    parts[-1] = parts[-1].replace('text-anchor="start"', 'text-anchor="middle"')
    row = result.get("selected") or result.get("closest_quoted_candidate")
    shortfall = result.get("shortfall_fen")
    rect(32, 116, 696, 160, "#ffedac", 24)
    text("复刻结果", 56, 151, 15, "#655a37")
    if shortfall:
        text("当前候选都超预算", 56, 207, 38, bold=True)
        text("已检查你接受的餐单，暂未找到预算内方案。", 56, 248, 18)
    elif row:
        text("需要替换，仍需确认" if row["differences"] else "首选餐单在预算内", 56, 207, 38, bold=True)
        text("所选规格还需核实，暂未下单。" if row["option_echo"] == "unknown" else
             "已找到预算内候选，等待你的决定。", 56, 248, 18)
    elif result.get("reason") == "no_approved_match":
        text("想吃的餐品不可售", 56, 207, 38, bold=True)
        text("本轮未核价，保留你的原有选择。", 56, 248, 18)
    else:
        text("核价尚未完成", 56, 207, 38, bold=True)
        text("先补齐报价，再判断能不能按原条件复刻。", 56, 248, 18)

    y = 320
    paragraph("餐单与变化", 21, "#20242a", x=32, bold=True)
    paragraph(recipe["name"], 16, x=32, width=680)
    y += 12
    if row:
        complete = result["quoted_count"] == result["candidate_count"]
        by_request = {fingerprint(r["request"]): r for r in receipts["quotes"]}
        candidates = job["requests"] if complete else []
        minimum_quote = min((r["response"]["data"]["price"] for r in receipts["quotes"]), default=None)
        for candidate in candidates:
            quote = by_request[candidate["request_fingerprint"]]["response"]["data"]["price"]
            names = []
            for item in candidate["cart"]:
                names.extend(wrap(f"{item['name']} × {item['quantity']}", 42))
            changes = []
            for change in candidate["differences"]:
                changes.extend(wrap(f"{change['from']} → {change['to']}", 72))
            height = 58 + len(names) * 28 + len(changes) * 25
            lowest = quote == minimum_quote
            rect(32, y, 696, height, "#ffffff", 18, "#c9a451" if lowest else "#e0e5eb")
            label = "已接受替代" if candidate["differences"] else "首选餐单"
            text(label + (" · 最低报价" if lowest else ""), 54, y + 30, 14, "#8b6826" if lowest else "#66707d")
            text(f"¥{money(quote)}", 704, y + 66, 29, bold=True, end=True)
            for index, name in enumerate(names):
                text(name, 54, y + 60 + index * 28, 19, "#20242a", index == 0)
            for index, change in enumerate(changes):
                text(change, 54, y + 60 + len(names) * 28 + index * 25, 15, "#66707d")
            y += height + 12
        if row["option_echo"] == "unknown":
            y += 12
            paragraph("所选规格还需确认", 17, "#8b6826", x=32, bold=True)
            paragraph("规格已提交，但核价结果未显示具体选配。", 16, x=32, width=680)
    elif result.get("missing_slot"):
        paragraph(f"缺少匹配：{result['missing_slot']}，已接受的候选当前不可售。", 18, x=32)

    y += 12
    rect(32, y, 696, 112 if shortfall else 86, "#ffffff", 18, "#e0e5eb")
    text("价格与预算", 54, y + 30, 17, bold=True)
    budget_line = f"你的预算 ¥{money(result['budget_fen'])}"
    if row:
        budget_line += f"   /   {'最低已核价' if shortfall else '所选餐单'} ¥{money(row['total_fen'])}"
    text(budget_line, 54, y + 59, 18, "#66707d")
    if shortfall:
        text(f"超出预算 ¥{money(shortfall)}，本轮未找到达标方案。", 54, y + 87, 17, "#66707d")
    y += 124 if shortfall else 98

    y += 12
    action_top = y
    rect(32, y, 696, 118, "#202b36", 20)
    text("下一步，先重新查价" if historical else "下一步，在对话里继续", 54, y + 36, 21, "#ffffff", True)
    text("告诉 Agent：重新查一下这份餐单。" if historical else
         "保留条件再查，或告诉 Agent 想调整哪些餐品。", 54, y + 72, 18, "#d9e1e9")
    text("任何改动都由你决定，确认后再重新核价。", 54, y + 98, 14, "#aebac6")
    y = action_top + 154
    observed = row["called_at"] if row else job["observed_at"]
    shown_time = timestamp(observed).strftime("%Y.%m.%d %H:%M %z")
    paragraph(f"{shown_time}  ·  已核价 {result['quoted_count']}/{result['candidate_count']} 份候选", 14, x=32)
    paragraph("报价需重查 · 未创建订单" if historical else "价格以本轮核价为准 · 未创建订单", 14, x=32)
    if result["mode"] == "simulated":
        paragraph("纯模拟 · 虚构数据", 14, x=32)
    if result.get("search_truncated"):
        paragraph("仅比较部分候选，其他组合尚未核查。", 14, x=32)
    height = y + 16
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="760" '
            f'height="{height}" viewBox="0 0 760 {height}" role="img" aria-labelledby="title desc">'
            '<title id="title">麦麦复刻结果卡</title>'
            f'<desc id="desc">{escape(badge)}；{escape(titles[result["status"]])}；未创建订单。</desc>'
            f'<rect width="760" height="{height}" fill="#f5f7fa"/>'
            '<g font-family="Microsoft YaHei, PingFang SC, Noto Sans CJK SC, sans-serif">'
            + "".join(parts) + '</g></svg>\n')
