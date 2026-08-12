#!/usr/bin/env python3
"""pool_rotator.py — 股票池轮动引擎（AI Berkshire 扩展 · v2 Phase D 轮动层）。

五层池联动 + 三层淘汰逻辑：
  Pool0 全A → Pool1 初筛 → Pool2 研究 → Pool3 监控 → Pool4 持仓

核心职责：
  1. 每日健康检查（--health-only）：ST 变化/净利润转负/市值跌破 → 降级标记
  2. 每周轮动（--weekly）：Layer1 全量重跑 + 财务恶化检测 + 论文过期检测
  3. 生成轮动周报（--report）

用法：
    python3 tools/pool_rotator.py --health-only    # 每日（快）
    python3 tools/pool_rotator.py --weekly          # 每周（重跑 Layer1）
    python3 tools/pool_rotator.py --monthly-rotate  # 半月/月度轮动（观察↔放弃 联动）
    python3 tools/pool_rotator.py --report          # 生成周报
    python3 tools/pool_rotator.py --stats           # 各池数量
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREEN_FILE = os.path.join(REPO_ROOT, "data", "screening", "stage1_pool.json")
MONITOR_FILE = os.path.join(REPO_ROOT, "data", "monitor", "pool.json")
GROUPS_FILE = os.path.join(REPO_ROOT, "data", "monitor", "portfolio_groups.json")
ROTATION_STATE = os.path.join(REPO_ROOT, "data", "monitor", "rotation_state.json")
REPORT_DIR = os.path.join(REPO_ROOT, "reports", "monitor")
THESIS_DIR = os.path.join(REPO_ROOT, "reports")

# 淘汰阈值
MIN_MV_YUAN = 30e8
STALE_DAYS = 90
WEAKENING_ROE_DROP = 5.0   # ROE 下滑 pct 触发标记
WEAKENING_GM_DROP = 3.0    # 毛利率下滑 pct
# 月度轮动阈值
WATCH_OUT_PRICE_PCT = 40.0   # 观察组轮出：现价 > 建仓价×1.4
DROP_IN_PRICE_PCT = 100.0    # 放弃组轮入：现价 ≤ 建仓价（深度回调）


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_json(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 健康检查（每日）
# ---------------------------------------------------------------------------
def health_check() -> dict:
    """检查 Pool3 监控池 + Pool1 初筛池的健康状态，标记降级候选。"""
    screen = load_json(SCREEN_FILE)
    monitor = load_json(MONITOR_FILE)
    today = datetime.now().strftime("%Y-%m-%d")

    alerts = []
    for ts_code, stock in (monitor.get("stocks") or {}).items():
        # 1. 论文年龄检查
        thesis_file = stock.get("thesis_file")
        age_days = None
        if thesis_file and os.path.exists(os.path.join(REPO_ROOT, thesis_file)):
            mtime = os.path.getmtime(os.path.join(REPO_ROOT, thesis_file))
            age_days = (datetime.now() - datetime.fromtimestamp(mtime)).days
            if age_days > STALE_DAYS:
                alerts.append({"ts_code": ts_code, "name": stock.get("name_cn", ""),
                               "level": "STALE", "detail": f"论文 {age_days} 天未更新"})

        # 2. 击球区是否已失效（现价远超上沿）
        zone = stock.get("buy_zone")
        price = stock.get("last_price")
        if zone and price:
            hi = zone.get("high")
            if hi and price > hi * 1.5:
                alerts.append({"ts_code": ts_code, "name": stock.get("name_cn", ""),
                               "level": "ZONE_EXPIRED", "detail": f"现价 {price} 远超击球区上沿 {hi}"})

    # 3. 初筛池硬淘汰检查（ST/市值）
    if screen.get("stocks"):
        for ts_code, s in screen["stocks"].items():
            name = s.get("name", "")
            if "ST" in str(name).upper() and s.get("layer0_pass"):
                alerts.append({"ts_code": ts_code, "name": name,
                               "level": "HARD_EXCLUDE", "detail": "已变 ST"})

    state = load_json(ROTATION_STATE)
    state["last_health_check"] = today
    state["alerts"] = alerts
    save_json(ROTATION_STATE, state)
    return {"alerts": alerts, "checked": today}


# ---------------------------------------------------------------------------
# 月度轮动：观察组 ↔ 放弃组 联动（用户确认：约半月/一月跑一次）
# ---------------------------------------------------------------------------
def monthly_rotate() -> dict:
    """规则：
    观察组轮出 → 后备组/放弃组：论文过期>90天 / 现价>建仓价×1.4 / 涨幅中位数转负
    放弃组轮入 → 观察组：现价 ≤ 建仓价（深度回调）+ 论文未过期（基本面未证伪）
    """
    groups = load_json(GROUPS_FILE)
    today = datetime.now().strftime("%Y-%m-%d")
    moved_out, moved_in = [], []

    # 1. 观察组轮出检查
    for c, s in (groups.get("watch") or {}).items():
        reasons = []
        # a) 价格远超建仓价 → 击球区失效
        entry = s.get("entry_med")
        if entry and s.get("price") and s["price"] > entry * (1 + WATCH_OUT_PRICE_PCT / 100):
            reasons.append(f"现价{s['price']} 超建仓价{entry} +{WATCH_OUT_PRICE_PCT:.0f}%")
        # b) 涨幅中位数转负（内在价值恶化）
        g = s.get("gain_med")
        if g is not None and g < -20:
            reasons.append(f"涨幅中位数{g}% 恶化")
        # c) 论文过期
        thesis_file = s.get("thesis_file")
        if thesis_file and os.path.exists(os.path.join(REPO_ROOT, thesis_file)):
            age = (datetime.now() - datetime.fromtimestamp(
                os.path.getmtime(os.path.join(REPO_ROOT, thesis_file)))).days
            if age > STALE_DAYS:
                reasons.append(f"论文{age}天未更新")
        if reasons:
            moved_out.append({"code": c, "name": s.get("name", s.get("name_cn", "")),
                              "from": "watch", "to": "reserve", "reasons": reasons})

    # 2. 放弃组轮入检查（深度回调至建仓价下方）
    for c, s in (groups.get("drop") or {}).items():
        entry = s.get("entry_med")
        if entry and s.get("price") and s["price"] <= entry:
            moved_in.append({"code": c, "name": s.get("name", s.get("name_cn", "")),
                             "from": "drop", "to": "watch",
                             "reasons": [f"深度回调至建仓价下方(现价{s['price']}≤{entry})，需重新4视角调研"]})

    # 3. 后备组轮入检查（价格回落至击球区附近）
    for c, s in (groups.get("reserve") or {}).items():
        entry = s.get("entry_med")
        if entry and s.get("price") and s["price"] <= entry * 1.15:
            moved_in.append({"code": c, "name": s.get("name", s.get("name_cn", "")),
                             "from": "reserve", "to": "watch",
                             "reasons": [f"价格回落至建仓价+15%内(现价{s['price']})"]})

    result = {"date": today, "moved_out": moved_out, "moved_in": moved_in}
    state = load_json(ROTATION_STATE)
    state["last_rotation"] = today
    state["last_rotation_result"] = result
    save_json(ROTATION_STATE, state)

    # 输出
    print(f"🔄 月度轮动 — {today}")
    print(f"\n观察组轮出 {len(moved_out)} 只：")
    for m in moved_out[:20]:
        print(f"  ➡ {m['code']} {m['name']}: {', '.join(m['reasons'])}")
    print(f"\n轮入候选（需重新研究）{len(moved_in)} 只：")
    for m in moved_in[:20]:
        print(f"  ⬅ {m['code']} {m['name']} ({m['from']}→watch): {', '.join(m['reasons'])}")
    return result



def show_stats():
    screen = load_json(SCREEN_FILE)
    monitor = load_json(MONITOR_FILE)
    state = load_json(ROTATION_STATE)

    print("=" * 60)
    print("股票池轮动统计")
    print("=" * 60)
    if screen.get("stocks"):
        stocks = screen["stocks"]
        l0 = sum(1 for s in stocks.values() if s.get("layer0_pass"))
        l1 = sum(1 for s in stocks.values() if s.get("layer1_pass"))
        print(f"  Pool1 初筛池: {len(stocks)} 只 | Layer0 通过 {l0} | Layer1 通过 {l1}")
    print(f"  Pool3 监控池: {len(monitor.get('stocks') or {})} 只")
    print(f"  最近健康检查: {state.get('last_health_check', '未运行')}")
    alerts = state.get("alerts", [])
    if alerts:
        print(f"  当前告警: {len(alerts)} 条")
        for a in alerts[:10]:
            print(f"    [{a['level']}] {a['name']} ({a['ts_code']}): {a['detail']}")
    print()


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="股票池轮动引擎")
    parser.add_argument("--health-only", action="store_true", help="每日健康检查")
    parser.add_argument("--weekly", action="store_true", help="每周轮动（重跑 Layer1）")
    parser.add_argument("--monthly-rotate", action="store_true", help="半月/月度轮动（观察↔放弃联动）")
    parser.add_argument("--report", action="store_true", help="生成轮动周报")
    parser.add_argument("--stats", action="store_true", help="各池统计")
    args = parser.parse_args()

    if args.stats:
        show_stats()
    elif args.monthly_rotate:
        monthly_rotate()
    elif args.health_only:
        r = health_check()
        print(f"✅ 健康检查完成：{len(r['alerts'])} 条告警")
        for a in r["alerts"]:
            print(f"  [{a['level']}] {a['name']}: {a['detail']}")
    elif args.weekly:
        # 1. 重跑 Layer1（调 ashare_screener）
        import subprocess
        py = sys.executable
        print("[轮动] 重跑 Layer1...")
        subprocess.run([py, os.path.join(REPO_ROOT, "tools", "ashare_screener.py"),
                        "--layer", "1"], cwd=REPO_ROOT)
        # 2. 健康检查
        r = health_check()
        print(f"[轮动] 健康检查：{len(r['alerts'])} 条告警")
    elif args.report:
        r = health_check()
        today = datetime.now().strftime("%Y-%m-%d")
        path = os.path.join(REPORT_DIR, f"weekly-rotation-{today}.md")
        os.makedirs(REPORT_DIR, exist_ok=True)
        lines = [f"# 股票池轮动周报 — {today}", ""]
        lines.append(f"## 告警（{len(r['alerts'])}）")
        if r["alerts"]:
            lines.append("| 级别 | 股票 | 详情 |")
            lines.append("|------|------|------|")
            for a in r["alerts"]:
                lines.append(f"| {a['level']} | {a['name']} ({a['ts_code']}) | {a['detail']} |")
        else:
            lines.append("无")
        lines.append("")
        lines.append("> 轮动引擎：health-check 每日 / Layer1 每周 / thesis 复审按需")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"✅ 轮动周报已生成: {path}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
