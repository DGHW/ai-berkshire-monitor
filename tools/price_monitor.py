#!/usr/bin/env python3
"""price_monitor.py — 监控池每日检查引擎（AI Berkshire 扩展）。

读取 data/monitor/pool.json → 逐只拉取最新价 → 状态机迁移
（WATCHING → TRIGGERED → REVIEW_DUE）→ 生成 reports/monitor/daily/{date}-monitor.md

状态机规则：
  - 未设置 buy_zone 的股票：只记录价格，保持 WATCHING
  - 现价进入 buy_zone（low ≤ price ≤ high）：
      第 1 天 → TRIGGERED（记录 triggered_since）
      连续 N 天（trigger_confirm_days，小盘股翻倍）→ REVIEW_DUE
  - 单日涨跌 > 8% 的股票：标记 need_news_pulse（建议 /news-pulse 归因）

零 LLM 依赖——纯脚本，每日运行 token 成本为 0。

用法：
    python3 tools/price_monitor.py            # 完整跑一遍并生成日报
    python3 tools/price_monitor.py --dry-run  # 只拉价格不写文件
    python3 tools/price_monitor.py --ticker 600519  # 只查一只
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quote_fetcher import get_spot  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL_FILE = os.path.join(REPO_ROOT, "data", "monitor", "pool.json")
DAILY_DIR = os.path.join(REPO_ROOT, "reports", "monitor", "daily")
ALERT_PCT = 8.0  # 单日涨跌警报阈值


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_pool() -> dict:
    with open(POOL_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_pool(pool: dict):
    with open(POOL_FILE, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)


def advance_state(stock: dict, price: float, add_price: float = None) -> str:
    """状态机：返回新状态。

    新增 8+4 分批（2026-08-19 用户确认）：
      BOUGHT 且现价 ≤ 补仓价（entry_min）→ ADD_DUE（补仓复核触发）
      ADD_DUE 且回升至补仓价上方 → 复位 BOUGHT
    """
    status = stock.get("status", "WATCHING")

    # 补仓触发：已持仓股票跌至补仓价
    if status in ("BOUGHT", "ADD_DUE") and add_price is not None:
        if price <= add_price:
            return "ADD_DUE"
        if status == "ADD_DUE":
            return "BOUGHT"
        return status

    zone = stock.get("buy_zone")
    if not zone:
        return status

    low, high = zone.get("low"), zone.get("high")
    in_zone = low is not None and high is not None and low <= price <= high

    if in_zone:
        # v7 规则变更（2026-08-14 用户确认）：取消 2 天连续确认，首次触发直接 REVIEW_DUE
        if status in ("WATCHING", "TRIGGERED"):
            stock["triggered_since"] = datetime.now().strftime("%Y-%m-%d")
            return "REVIEW_DUE"
        if status == "REVIEW_DUE":
            return "REVIEW_DUE"
        return status
    else:
        # 跌出击球区 → 复位
        if status in ("TRIGGERED", "REVIEW_DUE"):
            stock["triggered_since"] = None
            return "WATCHING"
        return status


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="监控池每日检查")
    parser.add_argument("--dry-run", action="store_true", help="只拉价格不写文件")
    parser.add_argument("--ticker", default=None, help="只检查指定股票")
    args = parser.parse_args()

    pool = load_pool()
    stocks = pool["stocks"]
    today = datetime.now().strftime("%Y-%m-%d")

    # 补仓价（8+4 分批）：从 groups 读 entry_min
    groups = {}
    gpath = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "monitor", "portfolio_groups.json")
    if os.path.exists(gpath):
        try:
            with open(gpath, encoding="utf-8") as f:
                groups = json.load(f)
        except (OSError, json.JSONDecodeError):
            groups = {}

    def get_add_price(ticker: str):
        for g in ("buy", "watch", "reserve", "drop"):
            s = groups.get(g, {}).get(ticker)
            if s and s.get("entry_min"):
                return s["entry_min"]
        return None

    rows = []
    errors = []

    for ticker, stock in stocks.items():
        if args.ticker and ticker != args.ticker:
            continue
        try:
            q = get_spot(ticker)
            price = q["price"]
            old_status = stock.get("status", "WATCHING")
            new_status = advance_state(stock, price, add_price=get_add_price(ticker))
            stock["status"] = new_status
            stock["last_check"] = today
            stock["last_price"] = price
            stock["last_price_date"] = q["ts"][:10]

            abs_change = abs(q.get("change_pct", 0))
            need_news = abs_change > ALERT_PCT
            stock["need_news_pulse"] = need_news

            rows.append({
                "ticker": ticker, "name": q["name_cn"], "price": price,
                "change_pct": q.get("change_pct", 0), "old": old_status,
                "new": new_status, "zone": stock.get("buy_zone"),
                "need_news": need_news,
            })
        except Exception as e:
            errors.append({"ticker": ticker, "error": str(e)})
            rows.append({"ticker": ticker, "name": "?", "price": None,
                         "change_pct": 0, "old": stock.get("status"),
                         "new": stock.get("status"), "zone": stock.get("buy_zone"),
                         "need_news": False, "error": str(e)})

    if not args.dry_run:
        save_pool(pool)

    # ---- 生成日报 ----
    review_due = [r for r in rows if r["new"] == "REVIEW_DUE"]
    triggered = [r for r in rows if r["new"] == "TRIGGERED"]
    add_due = [r for r in rows if r["new"] == "ADD_DUE"]
    near = []
    for r in rows:
        z = r["zone"]
        if z and r["price"] is not None and z.get("low") and z.get("high"):
            # 双向接近：计算到区间最近边界（下沿或上沿）的距离
            if r["price"] < z["low"]:
                dist = (z["low"] - r["price"]) / z["low"] * 100
                side = "下方"
            elif r["price"] > z["high"]:
                dist = (r["price"] - z["high"]) / z["high"] * 100
                side = "上方"
            else:
                continue  # 在区间内，由 TRIGGERED/REVIEW_DUE 处理
            if dist < 10:
                r["dist_pct"] = dist
                r["side"] = side
                near.append(r)
    need_news = [r for r in rows if r.get("need_news")]

    lines = []
    lines.append(f"# 监控池日报 — {today}")
    lines.append("")
    lines.append("> 数据源：腾讯/新浪免费实时 | 生成时间：" + datetime.now().strftime("%H:%M:%S"))
    lines.append("")

    lines.append(f"## 触发警报（{len(review_due)}）")
    if review_due:
        lines.append("| 股票 | 名称 | 现价 | 击球区 | 状态 |")
        lines.append("|------|------|------|--------|------|")
        for r in review_due:
            z = r["zone"]
            lines.append(f"| {r['ticker']} | {r['name']} | {r['price']:.2f} | "
                         f"{z['low']}-{z['high']} | REVIEW_DUE |")
        lines.append("")
        lines.append("**这些股票已连续确认进入击球区，需要执行 /thesis-drift 复审后再决定买入。**")
    else:
        lines.append("无")
    lines.append("")

    lines.append(f"## 补仓触发（{len(add_due)}）")
    if add_due:
        lines.append("| 股票 | 名称 | 现价 | 补仓价 | 状态 |")
        lines.append("|------|------|------|--------|------|")
        for r in add_due:
            lines.append(f"| {r['ticker']} | {r['name']} | {r['price']:.2f} | "
                         f"{get_add_price(r['ticker']):.2f} | ADD_DUE |")
        lines.append("")
        lines.append("**持仓股跌至补仓价，待 lite 复核（错杀则补仓 4%，证伪则不加）。**")
    else:
        lines.append("无")
    lines.append("")

    lines.append(f"## 首次触发（{len(triggered)}）")
    if triggered:
        lines.append("| 股票 | 名称 | 现价 | 击球区 | 需确认天数 |")
        lines.append("|------|------|------|--------|-----------|")
        for r in triggered:
            z = r["zone"]
            st = pool["stocks"][r["ticker"]]
            lines.append(f"| {r['ticker']} | {r['name']} | {r['price']:.2f} | "
                         f"{z['low']}-{z['high']} | {st.get('trigger_confirm_days', 2)} |")
    else:
        lines.append("无")
    lines.append("")

    lines.append(f"## 接近触发（距击球区 <10%，{len(near)}）")
    if near:
        lines.append("| 股票 | 名称 | 现价 | 距区间下限 |")
        lines.append("|------|------|------|-----------|")
        for r in sorted(near, key=lambda x: x["dist_pct"]):
            lines.append(f"| {r['ticker']} | {r['name']} | {r['price']:.2f} | "
                         f"距区间{ r['side'] } {r['dist_pct']:.1f}% |")
    else:
        lines.append("无")
    lines.append("")

    lines.append(f"## 单日异动 >{ALERT_PCT:.0f}%（建议 news-pulse 归因）")
    if need_news:
        for r in need_news:
            lines.append(f"- **{r['ticker']} {r['name']}** {r['change_pct']:+.2f}%")
    else:
        lines.append("无")
    lines.append("")

    lines.append("## 全部监控（" + str(len(rows)) + "）")
    lines.append("| 股票 | 名称 | 现价 | 涨跌% | 状态 |")
    lines.append("|------|------|------|-------|------|")
    for r in rows:
        p = f"{r['price']:.2f}" if r["price"] is not None else "ERR"
        err_mark = " ⚠️" if r.get("error") else ""
        lines.append(f"| {r['ticker']} | {r['name']} | {p} | {r['change_pct']:+.2f}% | "
                     f"{r['new']}{err_mark} |")
    lines.append("")

    if errors:
        lines.append("## 数据异常")
        for e in errors:
            lines.append(f"- {e['ticker']}: {e['error']}")
        lines.append("")

    report = "\n".join(lines)

    if args.dry_run:
        print(report)
        print(f"\n[dry-run] 未写文件。共 {len(rows)} 只，错误 {len(errors)} 只。")
        return

    os.makedirs(DAILY_DIR, exist_ok=True)
    path = os.path.join(DAILY_DIR, f"{today}-monitor.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"✅ 日报已写入: {path}")
    print(f"   监控 {len(rows)} 只 | 触发复审 {len(review_due)} | 首次触发 {len(triggered)} | 错误 {len(errors)}")


if __name__ == "__main__":
    main()
