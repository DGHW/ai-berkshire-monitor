#!/usr/bin/env python3
"""position_manager.py — 持仓管理与买卖闭环引擎（AI Berkshire · v3）。

闭环链路：
  WATCH(观察) → TRIGGERED(首日进击球区) → REVIEW_DUE(连续N日待复审)
      → [buy] 复审通过 → HOLD(持仓) → [sell] 止盈/止损/论文恶化 → 卖出
      → 空位由 RESERVE(后备)/DROP(放弃) 按轮动规则补入 WATCH

数据文件：
  data/positions/positions.json   — 持仓记录（成本/数量/日期/目标价/止损价）
  data/monitor/pool.json          — 监控池（状态机由 price_monitor 维护）
  data/monitor/portfolio_groups.json — 四组分类（buy/watch/reserve/drop）

用法：
  python3 tools/position_manager.py --add 600519 --shares 100 --price 1500  # 买入登记
  python3 tools/position_manager.py --sell 600519 --shares 100              # 卖出
  python3 tools/position_manager.py --daily                                  # 每日持仓巡检（止盈/止损/恶化）
  python3 tools/position_manager.py --rotate                                 # 轮动补位（watch 空位 ← reserve/drop）
  python3 tools/position_manager.py --report                                 # 持仓月报
  python3 tools/position_manager.py --list                                   # 列出持仓
"""

import argparse
import json
import os
import sys
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POS_FILE = os.path.join(REPO_ROOT, "data", "positions", "positions.json")
POOL_FILE = os.path.join(REPO_ROOT, "data", "monitor", "pool.json")
GROUPS_FILE = os.path.join(REPO_ROOT, "data", "monitor", "portfolio_groups.json")
WATCH_RANK = os.path.join(REPO_ROOT, "data", "monitor", "watch_rank.json")
REPORT_DIR = os.path.join(REPO_ROOT, "reports", "positions")

# 监控池活跃目标数（WATCHING/TRIGGERED/REVIEW_DUE 合计；BOUGHT/REMOVED 不计）
POOL_TARGET = 95

# 卖出规则阈值（用户确认 v5：止损 + 内在涨幅兑现止盈；不设固定止盈）
STOP_LOSS_PCT = -20.0      # 止损：相对成本 -20%
VALUATION_ALERT_PCT = 40.0  # 估值提醒：盈亏+40%时建议复核论文（非自动卖出）
REALIZE_TAKE_PROFIT = 0.8   # 内在涨幅兑现止盈：已实现涨幅 ≥ 内在涨幅×80% → 止盈
MIN_HOLD_DAYS = 5           # 最短持有天数（防止一日游）


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_json(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_positions() -> dict:
    return load_json(POS_FILE)


def save_positions(positions):
    save_json(POS_FILE, positions)


# ---------------------------------------------------------------------------
# 买入登记
# ---------------------------------------------------------------------------
def add_position(code: str, shares: int, price: float, reason: str = ""):
    positions = load_positions()
    now = datetime.now().strftime("%Y-%m-%d")
    if code in positions:
        # 加仓
        p = positions[code]
        total_cost = p["total_cost"] + shares * price
        p["shares"] += shares
        p["total_cost"] = total_cost
        p["avg_cost"] = round(total_cost / p["shares"], 4)
        p["last_add"] = now
        p["note"] = (p.get("note", "") + f"; 加仓{now}@{price}").strip("; ")
        print(f"✅ 加仓 {code} +{shares} 股 @{price}，持仓 {p['shares']} 股，均本 {p['avg_cost']}")
    else:
        positions[code] = {
            "name_cn": "",
            "shares": shares,
            "avg_cost": price,
            "total_cost": shares * price,
            "buy_date": now,
            "last_add": now,
            "reason": reason,
            "sell_reason": None,
            "sell_date": None,
            "note": "",
            "highest_price": price,
        }
        print(f"✅ 买入 {code} {shares} 股 @{price}，日期 {now}")

    # 从 pool 里同步名称
    pool = load_json(POOL_FILE)
    stocks = pool.get("stocks", {})
    if code in stocks and positions[code].get("name_cn", "") == "":
        positions[code]["name_cn"] = stocks[code].get("name_cn", "")
    # 记录内在涨幅（四视角中位数，供兑现止盈用）
    groups = load_json(os.path.join(REPO_ROOT, "data", "monitor", "portfolio_groups.json"))
    for g in ("buy", "watch", "reserve", "drop"):
        if code in groups.get(g, {}):
            gm = groups[g][code].get("gain_med")
            if gm is not None:
                positions[code]["gain_med"] = gm
            break
    save_positions(positions)

    # 凯利建议仓位（中位数偏差凯利）
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import pool_kelly
        groups = load_json(os.path.join(REPO_ROOT, "data", "monitor", "portfolio_groups.json"))
        basis = pool_kelly.load_basis()
        if not basis:
            basis = pool_kelly.pool_stats(groups)
        r = pool_kelly.kelly_for(code, groups, basis)
        if "error" not in r:
            print(f"🧮 中位数偏差凯利建议仓位: {r['suggested_pct']}% "
                  f"(超额内在涨幅 {r['delta']:+.1f}%，池中位 {r['pool_G_med']}%)")
        else:
            print(f"🧮 凯利建议: {r['error']}")
    except Exception as e:
        print(f"🧮 凯利建议计算失败: {e}")


# ---------------------------------------------------------------------------
# 卖出
# ---------------------------------------------------------------------------
def sell_position(code: str, shares: int, price: float, reason: str):
    positions = load_positions()
    if code not in positions:
        print(f"❌ 未持有 {code}")
        return
    p = positions[code]
    now = datetime.now().strftime("%Y-%m-%d")
    if shares >= p["shares"]:
        # 清仓
        p["shares"] = 0
        p["sell_date"] = now
        p["sell_reason"] = reason
        p["sell_price"] = price
        print(f"✅ 清仓 {code} @{price}，原因: {reason}")
    else:
        p["shares"] -= shares
        p["total_cost"] = p["avg_cost"] * p["shares"]
        print(f"✅ 部分卖出 {code} {shares} 股 @{price}，剩余 {p['shares']} 股")

    save_positions(positions)
    # 双轨：富途模拟盘同步卖出（失败仅告警，不影响本地记账）
    try:
        futu_cfg_file = os.path.join(REPO_ROOT, "data", "positions", "futu_config.json")
        if os.path.exists(futu_cfg_file):
            with open(futu_cfg_file, encoding="utf-8") as f:
                futu_cfg = json.load(f)
            if futu_cfg.get("enabled", True):
                from futu_bridge import _place_order
                res = _place_order(code, "SELL", shares, price, "ai-berkshire-sell", dry_run=False)
                if res.get("ok"):
                    print(f"📡 富途模拟盘卖出: {res.get('code')} {shares}股 status={res.get('status')}")
                else:
                    print(f"⚠️ 富途模拟盘卖出失败（不影响记账）: {res.get('reason', res.get('error', '?'))}")
    except Exception as e:
        print(f"⚠️ 富途模拟盘卖出异常（不影响记账）: {e}")


# ---------------------------------------------------------------------------
# 每日巡检：止盈/止损/论文恶化
# ---------------------------------------------------------------------------
def daily_check() -> list:
    from quote_fetcher import get_spot
    positions = load_positions()
    alerts = []
    today = datetime.now().strftime("%Y-%m-%d")

    for code, p in positions.items():
        if p.get("shares", 0) <= 0:
            continue
        try:
            q = get_spot(code)
            price = q["price"]
        except Exception as e:
            alerts.append({"code": code, "level": "ERR", "detail": str(e)})
            continue

        cost = p["avg_cost"]
        pnl_pct = (price / cost - 1) * 100
        p["last_price"] = price
        p["last_check"] = today
        if price > p.get("highest_price", cost):
            p["highest_price"] = price

        # 买入后最短持有期检查
        try:
            buy_date = datetime.strptime(p["buy_date"], "%Y-%m-%d")
            hold_days = (datetime.now() - buy_date).days
        except Exception:
            hold_days = 999
        if hold_days < MIN_HOLD_DAYS:
            continue

        # 止损（唯一自动卖出规则；不设止盈——盈利让子弹飞，靠基本面转换卖出）
        if pnl_pct <= STOP_LOSS_PCT:
            alerts.append({"code": code, "name": p.get("name_cn", ""), "level": "SELL",
                           "detail": f"止损触发 盈亏{pnl_pct:+.1f}% (成本{cost} 现价{price})",
                           "action": f"sell {code}"})
        # 论文估值提醒（非自动卖出）：现价已超论文内在价值中枢
        elif pnl_pct >= VALUATION_ALERT_PCT:
            alerts.append({"code": code, "name": p.get("name_cn", ""), "level": "REVIEW",
                           "detail": f"盈亏{pnl_pct:+.1f}% 已超论文估值中枢(+{VALUATION_ALERT_PCT}%)，"
                                     f"建议复核论文/基本面后决定是否卖出（不自动卖）",
                           "action": "review"})
        # 内在涨幅兑现止盈：现价涨幅 ≥ 该股内在涨幅的兑现比例
        gain = p.get("gain_med")
        if gain is not None and gain > 0:
            realized = pnl_pct / gain  # 已实现涨幅 / 内在涨幅 = 兑现比例
            if realized >= REALIZE_TAKE_PROFIT:
                alerts.append({"code": code, "name": p.get("name_cn", ""), "level": "SELL",
                               "detail": f"内在涨幅兑现 {realized*100:.0f}% "
                                         f"(盈亏{pnl_pct:+.1f}% / 内在涨幅{gain:.1f}%)，"
                                         f"已达兑现阈值 {REALIZE_TAKE_PROFIT*100:.0f}%，可止盈",
                               "action": "sell"})

    save_positions(positions)
    # 轻量新闻扫描（高置信冲击新闻 → 报告，不自动卖）
    try:
        from news_fetcher import fetch_multi
        for code in list(positions.keys()):
            if positions[code].get("shares", 0) <= 0:
                continue
            try:
                digest = fetch_multi(code, days=3)
                # 巨潮公告（一手披露）→ 高置信
                cninfo = digest.get("cninfo", []) or []
                for item in cninfo[:3]:
                    title = item.get("title", "")
                    alerts.append({"code": code, "name": positions[code].get("name_cn", ""),
                                   "level": "NEWS", "detail": f"巨潮公告: {title}",
                                   "action": "review"})
            except Exception:
                pass
    except Exception:
        pass
    return alerts


# ---------------------------------------------------------------------------
# 轮动补位：watch 空位 ← reserve/drop（按距建仓价最近优先）
# ---------------------------------------------------------------------------
def rotate() -> dict:
    groups = load_json(GROUPS_FILE)
    pool = load_json(POOL_FILE)
    stocks = pool.get("stocks", {})

    # 1. 补位目标：维持监控池活跃数（WATCHING/TRIGGERED/REVIEW_DUE）为 POOL_TARGET
    active = [c for c, s in stocks.items()
              if s.get("status") not in ("BOUGHT", "REMOVED")]
    need = max(0, POOL_TARGET - len(active))
    print(f"监控池活跃 {len(active)} 只（目标 {POOL_TARGET}）| 需补位 {need} 只")

    # 2. 从 reserve/drop 按距建仓价最近补位
    candidates = []
    for g in ("reserve", "drop"):
        for c, s in groups.get(g, {}).items():
            if c in stocks:
                continue
            ratio = s["price"] / s["entry_med"] if s.get("entry_med") else 999
            candidates.append((ratio, c, s, g))
    candidates.sort(key=lambda x: x[0])
    filled = []
    for _, c, s, g in candidates[:need]:
        stocks[c] = {
            "name_cn": s["name"],
            "group": "WATCH",
            "buy_zone": {"low": round(s["entry_med"] * 0.85, 2), "high": s["entry_med"]},
            "entry_price": s["entry_med"],
            "thesis_file": s.get("thesis_file"),
            "is_small_cap": c.startswith(("920", "8", "4")),
            "trigger_confirm_days": 2,
            "status": "WATCHING",
            "note": f"轮动补位自{g}组",
        }
        filled.append((c, s["name"], g))
    if filled:
        pool["stocks"] = stocks
        pool["updated"] = datetime.now().strftime("%Y-%m-%d")
        save_json(POOL_FILE, pool)
        print(f"🔄 轮动补位 {len(filled)} 只：")
        for c, n, g in filled:
            print(f"   {c} {n} ← {g}组")

    # 3. groups/pool 一致性修复：pool 标 WATCH（活跃监控）但 groups 不在 watch 的 → 同步组移动
    moved = []
    groups_dirty = False
    for c, s in stocks.items():
        if s.get("group") != "WATCH" or s.get("status") in ("BOUGHT", "REMOVED"):
            continue
        if c in groups.get("watch", {}):
            continue
        for g in ("reserve", "drop", "buy"):
            if c in groups.get(g, {}):
                item = groups[g].pop(c)
                groups.setdefault("watch", {})[c] = item
                moved.append((c, g))
                groups_dirty = True
                break
    if groups_dirty:
        groups["updated"] = datetime.now().strftime("%Y-%m-%d")
        save_json(GROUPS_FILE, groups)
        for c, g in moved:
            print(f"   🔧 一致性修复: {c} 从 {g} 组同步移入 watch")

    if not filled and not moved:
        print("🔄 无需补位")
    return {"filled": filled, "moved": moved}


# ---------------------------------------------------------------------------
# 报表
# ---------------------------------------------------------------------------
def generate_report() -> str:
    positions = load_positions()
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# 持仓月报 — {today}", ""]
    active = {c: p for c, p in positions.items() if p.get("shares", 0) > 0}
    closed = {c: p for c, p in positions.items() if p.get("shares", 0) == 0}
    lines.append(f"## 当前持仓（{len(active)}）")
    if active:
        lines.append("| 代码 | 名称 | 股数 | 成本 | 现价 | 盈亏% | 买入日 | 原因 |")
        lines.append("|------|------|------|------|------|-------|--------|------|")
        for c, p in active.items():
            pnl = (p.get("last_price", p["avg_cost"]) / p["avg_cost"] - 1) * 100
            lines.append(f"| {c} | {p.get('name_cn','')} | {p['shares']} | {p['avg_cost']} | "
                         f"{p.get('last_price','-')} | {pnl:+.1f}% | {p['buy_date']} | {p.get('reason','')} |")
    else:
        lines.append("无")
    lines.append("")
    lines.append(f"## 历史卖出（{len(closed)}）")
    for c, p in closed.items():
        lines.append(f"- {c} {p.get('name_cn','')}: {p.get('sell_date','?')} 卖出，原因 {p.get('sell_reason','?')}")
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"{today}-positions.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"✅ 持仓报告: {path}")
    return path


def review_stock(code: str):
    """深度复核入口：REVIEW_DUE 触发后，开 4 视角重审（调用 investment-team 技能）+
    新闻扫描验证基本面是否改观。复核结论写入 rotation_state。"""
    today = datetime.now().strftime("%Y-%m-%d")
    pool = load_json(POOL_FILE)
    stocks = pool.get("stocks", {})
    info = stocks.get(code, {})
    print(f"🔍 深度复核 — {code} {info.get('name_cn','')}")
    print(f"   状态: {info.get('status','?')} | 击球区: {info.get('buy_zone')}")
    print(f"   论文: {info.get('thesis_file','无')}")
    print()
    print("步骤：")
    print("  1️⃣ 调 /investment-team 对 {code} 重新做 4 视角研究（段永平/巴菲特/芒格/李录）")
    print("  2️⃣ 对比新旧研究：基本面是否改观？")
    print("     - 基本面没改观 + 价格仍在击球区 → 移入买入组，执行 --add 建仓")
    print("     - 基本面恶化（业绩/治理/行业证伪）→ 降级至放弃组")
    print("  3️⃣ 用 news_fetcher 扫描近 7 日新闻辅助判断")
    print()
    # 记录复核请求
    state = load_json(os.path.join(REPO_ROOT, "data", "monitor", "rotation_state.json"))
    reviews = state.setdefault("reviews", [])
    reviews.append({"code": code, "date": today, "status": "PENDING"})
    save_json(os.path.join(REPO_ROOT, "data", "monitor", "rotation_state.json"), state)
    print("✅ 复核已登记，等待 4 视角结果后回填结论")



    positions = load_positions()
    if not positions:
        print("无持仓记录")
        return
    print(f"{'代码':<8}{'名称':<10}{'股数':>6}{'成本':>10}{'现价':>10}{'盈亏%':>8}{'买入日':>12}")
    for c, p in positions.items():
        if p.get("shares", 0) <= 0:
            continue
        pnl = (p.get("last_price", p["avg_cost"]) / p["avg_cost"] - 1) * 100
        print(f"{c:<8}{p.get('name_cn',''):<10}{p['shares']:>6}{p['avg_cost']:>10.2f}"
              f"{p.get('last_price','-'):>10}{pnl:>+8.1f}%{p['buy_date']:>12}")


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="持仓管理与买卖闭环引擎")
    parser.add_argument("--add", nargs=3, metavar=("CODE", "SHARES", "PRICE"), help="买入登记")
    parser.add_argument("--sell", nargs=3, metavar=("CODE", "SHARES", "PRICE"), help="卖出")
    parser.add_argument("--reason", default="", help="卖出/买入原因")
    parser.add_argument("--daily", action="store_true", help="每日持仓巡检")
    parser.add_argument("--rotate", action="store_true", help="轮动补位")
    parser.add_argument("--review", nargs=1, metavar="CODE", help="深度复核（REVIEW_DUE→开4视角重审）")
    parser.add_argument("--report", action="store_true", help="持仓月报")
    parser.add_argument("--list", action="store_true", help="列出持仓")
    args = parser.parse_args()

    if args.add:
        add_position(args.add[0], int(args.add[1]), float(args.add[2]), args.reason)
    elif args.sell:
        sell_position(args.sell[0], int(args.sell[1]), float(args.sell[2]), args.reason or "手动")
    elif args.review:
        review_stock(args.review[0])
    elif args.daily:
        alerts = daily_check()
        if alerts:
            print(f"⚠️ {len(alerts)} 条卖出信号:")
            for a in alerts:
                print(f"  [{a['level']}] {a.get('name','')}({a['code']}): {a['detail']}")
        else:
            print("✅ 无卖出信号")
    elif args.rotate:
        rotate()
    elif args.report:
        generate_report()
    elif args.list:
        list_positions()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
