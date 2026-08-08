#!/usr/bin/env python3
"""industry_neutralize.py — 行业中性化筛选（AI Berkshire 扩展）。

解决"绝对阈值行业偏倚"：统一阈值对低毛利/重资产行业不公平
（建筑工程通过率1.6%、白酒轻松过、银行靠豁免）。

方法：行业内百分位排名，选"每个行业里的优等生"而非"所有行业共用一个及格线"。
  绝对底线（保留，防垃圾）→ 行业内百分位（优中选优）

用法：
    python3 tools/industry_neutralize.py --analyze     # 分析当前池的行业偏倚
    python3 tools/industry_neutralize.py --apply       # 应用中性化，写回 stage1_pool.json
    python3 tools/industry_neutralize.py --compare     # 对比 绝对阈值 vs 中性化 结果
    python3 tools/industry_neutralize.py --percentile 70  # 自定义行业线（默认前30%）
"""

import argparse
import json
import os
import sys
from collections import Counter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREEN_FILE = os.path.join(REPO_ROOT, "data", "screening", "stage1_pool.json")

# 中性化维度及权重
DIMENSIONS = [
    ("roe", "ROE", 0.40),           # 年化 ROE
    ("gross_margin", "毛利率", 0.25),
    ("net_margin", "净利率", 0.20),
    ("rev_yoy", "营收增速", 0.15),
]
INDUSTRY_MIN_N = 10        # 行业样本 <10 只 → 合并到"其他"避免过拟合
MIN_INDUSTRY_PCT = 70      # 行业内前 30%（百分位 ≥70）通过
MIN_INDUSTRY_N_FOR_PCT = 5  # 行业内至少 5 只有效数据才算百分位


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# 绝对底线（任何情况都剔除，防垃圾）
def abs_bottom_line(s: dict) -> list:
    """返回不满足的底线项列表（空 = 通过底线）。"""
    fails = []
    if s.get("latest_n_income") is not None and s["latest_n_income"] <= 0:
        fails.append("净利润<=0")
    if "ST" in str(s.get("name", "")).upper():
        fails.append("ST")
    mv = s.get("total_mv") or 0
    if mv and mv < 30e8:
        fails.append("市值<30亿")
    return fails


def industry_of(s: dict, mapping: dict = None) -> str:
    ind = s.get("industry", "其他")
    return ind or "其他"


def _pct_rank(values, target):
    """target 在 values 中的百分位（0-100），越大越好。"""
    if not values:
        return 50.0
    below = sum(1 for v in values if v < target)
    equal = sum(1 for v in values if v == target)
    rank = (below + 0.5 * equal) / len(values) * 100
    return rank


def compute_neutralized(screen: dict, min_pct: int = MIN_INDUSTRY_PCT) -> dict:
    """对通过绝对底线的股票，按行业内百分位重新评估。
    返回 {"stocks": {ts: {neutral_pct, neutral_score, neutral_pass, neutral_reasons}},
          "stats": {...}}
    """
    stocks = screen["stocks"]

    # 1. 行业分组（有效财务数据）
    groups = {}
    for ts, s in stocks.items():
        ind = industry_of(s)
        groups.setdefault(ind, []).append(ts)

    # 小行业合并
    merged = {}
    for ind, members in groups.items():
        target = ind if len(members) >= INDUSTRY_MIN_N else "其他"
        merged.setdefault(target, []).extend(members)

    result = {}
    stats = {"total": len(stocks), "passed_bottom": 0, "passed_neutral": 0,
             "industry_count": len(merged)}

    for ind, members in merged.items():
        # 该行业所有股票
        ind_stocks = [(ts, stocks[ts]) for ts in members]

        # 2. 绝对底线
        bottom_pass = [(ts, s) for ts, s in ind_stocks
                       if not abs_bottom_line(s)]
        stats["passed_bottom"] += len(bottom_pass)

        # 3. 行业内百分位
        # 每个维度：收集行业内有效值
        dim_vals = {}
        for dim, label, w in DIMENSIONS:
            vals = []
            for ts, s in bottom_pass:
                v = s.get(dim)
                if v is not None and v == v:  # not NaN
                    vals.append(v)
            dim_vals[dim] = vals

        for ts, s in bottom_pass:
            scores = {}
            for dim, label, w in DIMENSIONS:
                vals = dim_vals[dim]
                v = s.get(dim)
                if v is None or v != v:
                    scores[dim] = None
                    continue
                # 行业样本不足 → 退回绝对阈值判断（用中性值近似）
                if len(vals) < MIN_INDUSTRY_N_FOR_PCT:
                    # 兜底：直接用原始值打分（行业内无参照）
                    scores[dim] = 50.0 if v > 0 else 30.0
                else:
                    scores[dim] = _pct_rank(vals, v)

            # 综合得分（有效维度加权）
            valid = [(dim, scores[dim]) for dim, _, _ in DIMENSIONS
                     if scores.get(dim) is not None]
            if not valid:
                result[ts] = {"neutral_pct": None, "neutral_score": 0,
                              "neutral_pass": False, "neutral_reasons": ["无有效财务数据"]}
                continue
            total_w = sum(w for dim, _, w in DIMENSIONS if scores.get(dim) is not None)
            score = sum(scores[dim] * w for dim, _, w in DIMENSIONS
                        if scores.get(dim) is not None) / total_w if total_w else 0

            passed = score >= min_pct
            reasons = []
            if not passed:
                reasons.append(f"行业综合分 {score:.0f} < {min_pct}（前{100-min_pct}%线）")
            result[ts] = {
                "neutral_pct": round(score, 1),
                "neutral_score": round(score, 1),
                "neutral_pass": passed,
                "neutral_reasons": reasons,
            }
            if passed:
                stats["passed_neutral"] += 1

    stats["industry_count"] = len(merged)
    return {"stocks": result, "stats": stats}


def apply(screen: dict, min_pct: int) -> dict:
    """把中性化结果写回 screen 的每只股票。"""
    neut = compute_neutralized(screen, min_pct)
    stocks = screen["stocks"]
    for ts, s in stocks.items():
        n = neut["stocks"].get(ts)
        if n:
            s["neutral_pct"] = n["neutral_pct"]
            s["neutral_pass"] = n["neutral_pass"]
            s["neutral_reasons"] = n["neutral_reasons"]
        else:
            # 未过底线或未评估
            if not abs_bottom_line(s):
                s["neutral_pass"] = False
                s["neutral_reasons"] = ["未评估"]
    screen["neutral_stats"] = neut["stats"]
    screen["neutral_min_pct"] = min_pct
    screen["neutral_ts"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")
    return screen


def analyze(screen: dict):
    """展示当前行业偏倚。"""
    stocks = screen["stocks"]
    all_inds = Counter(s.get("industry", "未知") for s in stocks.values())
    pass_inds = Counter(s.get("industry", "未知") for s in stocks.values()
                        if s.get("layer1_pass"))
    print("=== 当前绝对阈值筛选的行业偏倚 ===")
    print(f"{'行业':<12}{'总数':>6}{'通过':>6}{'通过率':>8}")
    rows = []
    for ind in all_inds:
        total = all_inds[ind]
        passed = pass_inds.get(ind, 0)
        if total >= 20:
            rows.append((ind, total, passed, passed / total * 100))
    rows.sort(key=lambda x: -x[3])
    for ind, total, passed, rate in rows[:10]:
        print(f"{ind:<12}{total:>6}{passed:>6}{rate:>7.1f}%")
    print("...")
    rows.sort(key=lambda x: x[3])
    for ind, total, passed, rate in rows[:5]:
        print(f"{ind:<12}{total:>6}{passed:>6}{rate:>7.1f}%")


def compare(screen: dict, min_pct: int):
    """对比 绝对阈值 vs 中性化。"""
    neut = compute_neutralized(screen, min_pct)
    stocks = screen["stocks"]
    stats = neut["stats"]

    # 当前绝对阈值通过
    abs_pass = sum(1 for s in stocks.values() if s.get("layer1_pass"))

    print("=" * 60)
    print("行业中性化 vs 绝对阈值 对比")
    print("=" * 60)
    print(f"  总股票数:           {stats['total']}")
    print(f"  绝对阈值通过:       {abs_pass}")
    print(f"  中性化通过(底线后): {stats['passed_neutral']}")
    print(f"  通过绝对底线:       {stats['passed_bottom']}")
    print(f"  行业分组数:         {stats['industry_count']}")
    print()

    # 行业分布对比（中性化通过 vs 绝对通过）
    neut_pass_ind = Counter()
    abs_pass_ind = Counter()
    for ts, s in stocks.items():
        ind = s.get("industry", "其他")
        if s.get("layer1_pass"):
            abs_pass_ind[ind] += 1
        if neut["stocks"].get(ts, {}).get("neutral_pass"):
            neut_pass_ind[ind] += 1

    print(f"{'行业':<12}{'绝对':>6}{'中性':>6}{'差异':>8}")
    all_inds = set(abs_pass_ind) | set(neut_pass_ind)
    rows = []
    for ind in all_inds:
        if abs_pass_ind.get(ind, 0) + neut_pass_ind.get(ind, 0) >= 5:
            rows.append((ind, abs_pass_ind.get(ind, 0), neut_pass_ind.get(ind, 0)))
    rows.sort(key=lambda x: -abs(x[1] - x[2]))
    for ind, a, n in rows[:12]:
        diff = n - a
        flag = "↑补回" if diff > 0 else ("↓剔出" if diff < 0 else "")
        print(f"{ind:<12}{a:>6}{n:>6}{diff:>+8} {flag}")


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="行业中性化筛选")
    parser.add_argument("--analyze", action="store_true", help="分析行业偏倚")
    parser.add_argument("--apply", action="store_true", help="应用中性化并写回")
    parser.add_argument("--compare", action="store_true", help="对比两种筛选")
    parser.add_argument("--percentile", type=int, default=MIN_INDUSTRY_PCT,
                        help="行业通过线（百分位，默认70=前30%）")
    args = parser.parse_args()

    with open(SCREEN_FILE, encoding="utf-8") as f:
        screen = json.load(f)

    if args.analyze:
        analyze(screen)
    elif args.compare:
        compare(screen, args.percentile)
    elif args.apply:
        screen = apply(screen, args.percentile)
        with open(SCREEN_FILE, "w", encoding="utf-8") as f:
            json.dump(screen, f, ensure_ascii=False, indent=2)
        print(f"✅ 中性化已写回：{screen['neutral_stats']['passed_neutral']} 只通过（前{100-args.percentile}%）")
        print(f"   绝对底线通过 {screen['neutral_stats']['passed_bottom']} 只")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
