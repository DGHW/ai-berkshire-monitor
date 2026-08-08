#!/usr/bin/env python3
"""extract_targets.py — 从深度研究报告提取击球区与仓位（AI Berkshire 扩展）。

读取 /investment-research 或 /investment-team 产出的 Markdown 报告，
自动解析：
  1. 三情景估值（乐观/中性/悲观目标价 + 概率）—— 支持多种格式
  2. 信息丰富度评级（A/B/C）
  3. 综合评分 / 信心度
  4. 分层操作建议表 → 推导击球区（buy_zone）
  5. 联动 kelly_sizer.py 计算半凯利仓位
  6. 写入 data/monitor/pool.json

用法：
    python3 tools/extract_targets.py --report reports/腾讯/腾讯-research-20260620.md
    python3 tools/extract_targets.py --report reports/腾讯/腾讯-research-20260620.md --ticker 0700.HK --dry-run
    python3 tools/extract_targets.py --report reports/腾讯/腾讯-research-20260620.md --pool data/monitor/pool.json
"""

import argparse
import json
import os
import re
import sys
from decimal import Decimal

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_POOL = os.path.join(REPO_ROOT, "data", "monitor", "pool.json")

# ---------------------------------------------------------------------------
# 1. 信息丰富度评级
# ---------------------------------------------------------------------------
_INFO_GRADE_RE = re.compile(
    r"信息丰富度评级[：:]\s*([ABC])级", re.I
)

# ---------------------------------------------------------------------------
# 2. 综合评分（多种写法）
# ---------------------------------------------------------------------------
_SCORE_PATTERNS = [
    # 综合评分：3.8 / 5
    re.compile(r"综合评分[：:]\s*([\d.]+)\s*/\s*5"),
    # 综合评分 X / 5（表格行）
    re.compile(r"综合评分[：:]\s*([\d.]+)"),
    # ★★★★☆（4.3/5） 或 4.3/5
    re.compile(r"[（(]([\d.]+)\s*/\s*5[）)]"),
]

# ---------------------------------------------------------------------------
# 3. 三情景估值
# ---------------------------------------------------------------------------
# 完整表格格式（financial_rigor.py three-scenario 输出）：
#   | **乐观**（25%概率） | +15% | 15x | $14.24 | **$213.5** | +158.8% |
#   | **中性**（50%概率） | +8%  | 10x | $11.79 | **$117.9** | +42.9% |
#   | **悲观**（25%概率） | -5%  | 7x  | $8.03  | **$56.2**  | -31.9% |
# 简化格式：
#   | 乐观 | 15% | $26.13 | 25x | **$653** | +161% |
#
# 注意：必须逐行扫描（表格行以 | 开头），禁止跨行匹配，否则会误匹配
# 正文里的"乐观情景的类比"等句子。
_SCENARIO_KEYWORDS = {
    "乐观": "bull", "bull": "bull",
    "中性": "base", "base": "base", "neutral": "base",
    "悲观": "bear", "熊市": "bear", "bear": "bear",
    "牛市": "bull",
}

# 概率：行内"（25%概率）"或"25%概率"
_PROB_IN_ROW_RE = re.compile(r"(\d+)\s*%")
# 目标价：行内第一个带 $/HK$/¥/€ 的数字，或最后几列里的数字
_TARGET_PRICE_IN_ROW = re.compile(
    r"\*?\*?[¥$€HK$]\s*([\d,，.]+)\s*\*?\*?|目标价[^\d]*([\d,，.]+)"
)

# 自由文本情景：#### 中性情景（概率50%）：... 每股中性价值 = 927港元
_TEXT_SCENARIO_RE = re.compile(
    r"(乐观|中性|悲观|牛市|熊市)情景[（(]?\s*概率\s*(\d+)\s*%?[）)]?"
    r"[^\n]*?(?:每股|目标价|价值)[^\n]*?[=＝]?\s*[¥$€HK$]?\s*([\d,，.]+)\s*(港元|港币|HKD|美元|USD|元|人民币)?",
    re.I,
)

# 加权目标价（可作中性参考）：| 加权目标价 | — | — | — | **$126.4** | +53.2% |
_WEIGHTED_PRICE_RE = re.compile(
    r"加权目标价[^\|]*\|\s*[^|]*\|[^|]*\|[^|]*\|\s*\*?\*?[¥$€HK$]?\s*([\d,，.]+)",
    re.I,
)


def _row_cells(line: str) -> list[str]:
    """把表格行拆成单元格（去 markdown 修饰）。"""
    if not line.strip().startswith("|"):
        return []
    return [c.strip().strip("*_~").strip() for c in line.split("|")][1:-1]


def extract_scenarios(text: str) -> dict | None:
    """逐行扫描表格，提取 bull/base/bear 目标价与概率。"""
    rows: dict[str, dict] = {}
    price_col = None  # 表头中"目标价/目标股价"列索引

    for line in text.split("\n"):
        cells = _row_cells(line)

        # 检测表头：包含"情景"或"目标价" → 记录目标价列位置
        if cells and any("情景" in c or "目标" in c for c in cells):
            for i, c in enumerate(cells):
                if "目标价" in c or "目标股价" in c or "目标" == c:
                    price_col = i
            continue
        # 分隔行
        if line.strip().startswith("|") and re.fullmatch(r"[\|\s:\-]+", line.strip()):
            continue
        if not cells:
            price_col = None  # 离开表格
            continue

        first = cells[0]
        key = None
        for kw, k in _SCENARIO_KEYWORDS.items():
            if first.startswith(kw) or first == kw:
                key = k
                break
        if not key:
            # 行内可能嵌了关键词（如 "乐观（25%概率）"）
            for kw, k in _SCENARIO_KEYWORDS.items():
                if kw in first:
                    key = k
                    break
        if not key:
            continue

        # 概率：第一列里的"25%概率"或"乐观 40%"
        prob = None
        m = re.search(r"(\d+)\s*%", first)
        if m:
            prob = int(m.group(1))

        # 目标价提取（按优先级）：
        price = None
        # ① 表头指定的目标价列
        if price_col is not None and price_col < len(cells):
            m = re.search(r"([\d,，.]+)", cells[price_col])
            if m:
                price = _clean_num(m.group(1))
        # ② 带货币符号的数字（$23.0 / 23.0元 中的"元"不是货币前缀，跳过）
        if price is None:
            for c in reversed(cells[1:]):
                mm = re.search(r"[\¥\$€HK$]\s*([\d,，.]+)", c)
                if mm:
                    price = _clean_num(mm.group(1))
                    break
        # ③ 最后两个单元格中的数值（目标股价 vs 涨跌幅，取目标价列即倒数第二个）
        if price is None:
            nums = []
            for c in cells[1:]:
                mm = re.search(r"([\d,，.]+)", c)
                if mm:
                    nums.append(_clean_num(mm.group(1)))
            if nums:
                # 典型表：| 增速 | PE | EPS | 目标价 | 涨跌幅 | → 目标价是倒数第2个
                price = nums[-2] if len(nums) >= 2 else nums[-1]

        if price is not None and key not in rows:
            rows[key] = {"price": price, "prob": prob}

    # 文本情景兜底（#### 中性情景（概率50%）：... 每股价值 = 927港元）
    if not rows:
        for m in _TEXT_SCENARIO_RE.finditer(text):
            tier = m.group(1).lower()
            prob = int(m.group(2)) if m.group(2) else None
            price = _clean_num(m.group(3))
            key = "bull" if tier in ("乐观", "牛市") else ("bear" if tier in ("悲观", "熊市") else "base")
            if key not in rows:
                rows[key] = {"price": price, "prob": prob}

    # 加权目标价作为 base 兜底
    if "base" not in rows:
        m = _WEIGHTED_PRICE_RE.search(text)
        if m:
            rows["base"] = {"price": _clean_num(m.group(1)), "prob": None}

    # 概率补全：缺省时默认 25/50/25
    default_probs = {"bull": 0.25, "base": 0.50, "bear": 0.25}
    filled = {}
    for key, val in rows.items():
        p = val["prob"]
        if p is None:
            p = default_probs.get(key, 0.25)
        else:
            p = p / 100.0
        filled[key] = {"price": val["price"], "prob": p}

    if not filled:
        return None
    # 归一化概率
    total = sum(v["prob"] for v in filled.values())
    if total > 0 and abs(total - 1) > 0.001:
        for v in filled.values():
            v["prob"] = v["prob"] / total
    return filled

# ---------------------------------------------------------------------------
# 4. 分层操作建议表 → 击球区
# ---------------------------------------------------------------------------
# | 激进型 | 现价买入+跌破410加仓 | 431港元以下分批... |
# | 稳健型 | 现价建底仓1/2... | 底仓431以下；补仓触发... |
# | 保守型 | 观望... | 若回到390-410... |
_BUY_ZONE_CANDIDATES = [
    # 价格区间：400-490 / 390-410 / $75-85（排除百分比区间如 25-30%）
    re.compile(r"([\d,，.]+)\s*[-–—至到]\s*([\d,，.]+)(?!\s*%)"),
    # "xxx以下" / "低于xxx" → 上限
    re.compile(r"([\d,，.]+)\s*(?:以下|港元以下|以下分批|以下均)"),
    # "跌破xxx" / "低于xxx" → 上限
    re.compile(r"(?:跌破|低于|回到)\s*([\d,，.]+)"),
    # "xxx以上" → 下限
    re.compile(r"([\d,，.]+)\s*(?:以上|以上分批)"),
]

# 稳健型优先（middle-of-road），其次激进型，再保守型
_TIER_ORDER = ["稳健", "保守", "激进"]


def _clean_num(s: str) -> float:
    return float(s.replace(",", "").replace("，", ""))


def extract_info_grade(text: str) -> str | None:
    m = _INFO_GRADE_RE.search(text)
    return m.group(1).upper() if m else None


def extract_score(text: str) -> float | None:
    for pat in _SCORE_PATTERNS:
        m = pat.search(text)
        if m:
            v = float(m.group(1))
            if 0 < v <= 5:
                return v
    return None



def extract_buy_zone(text: str) -> dict | None:
    """从分层操作建议表提取击球区。优先稳健型，其次保守/激进。"""
    # 找"分层操作建议"或"投资建议"章节后的表格
    tier_rows = {}
    lines = text.split("\n")
    in_advice = False
    for line in lines:
        if re.search(r"分层操作建议|投资建议|操作建议", line):
            in_advice = True
            continue
        if not in_advice:
            continue
        # 进入新的一级/二级章节（# / ## / ### 开头且不含"建议"）且已过表头 → 结束
        if line.startswith("#") and not re.search(r"建议|催化剂", line):
            # 已经收集到分层行就停；没收集到则继续找（可能"定性判断"在分层表前面）
            if tier_rows:
                break
            continue
        if line.startswith("|") and not re.fullmatch(r"[\|\s:\-]+", line.strip()):
            for tier in _TIER_ORDER:
                if f"| {tier}" in line or f"|**{tier}" in line:
                    tier_rows[tier] = line
                    break

    # 从每行提取区间
    candidates = []
    for tier in _TIER_ORDER:
        if tier not in tier_rows:
            continue
        line = tier_rows[tier]
        ranges = []
        for pat in _BUY_ZONE_CANDIDATES:
            for m in pat.finditer(line):
                if m.lastindex == 2:  # 区间
                    lo, hi = _clean_num(m.group(1)), _clean_num(m.group(2))
                    if 0 < lo < hi:
                        ranges.append((lo, hi))
                elif m.lastindex == 1:  # 单侧
                    v = _clean_num(m.group(1))
                    ranges.append((v, v))
        if ranges:
            # 取最宽的区间作为击球区
            ranges.sort(key=lambda r: r[1] - r[0], reverse=True)
            candidates.append((tier, ranges[0]))

    if candidates:
        # 优先级：稳健 > 保守 > 激进
        tier_rank = {t: i for i, t in enumerate(_TIER_ORDER)}
        candidates.sort(key=lambda c: tier_rank.get(c[0], 9))
        lo, hi = candidates[0][1]
        return {"low": lo, "high": hi, "source_tier": candidates[0][0]}

    # 兜底：从空仓者建议段落找区间
    m = re.search(r"建议在?[¥$€HK$]?\s*([\d,，.]+)\s*[-–—至到]\s*([\d,，.]+)\s*区间", text)
    if m:
        lo, hi = _clean_num(m.group(1)), _clean_num(m.group(2))
        return {"low": lo, "high": hi, "source_tier": "text"}

    return None


def current_price_from_text(text: str) -> float | None:
    """尝试从报告头部提取当前价（用于计算收益率）。支持 **股价**：18.50元 格式。"""
    # **股价**：18.50元 / 股价：$82.52 / 现价 18.50
    m = re.search(r"[*_]*\s*(?:股价|现价|当前价|最新价)[*_]*\s*[：:]\s*[~约]?\s*[¥$€HK$]?\s*([\d,，.]+)", text)
    if m:
        return _clean_num(m.group(1))
    return None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="从深度报告提取击球区与仓位")
    parser.add_argument("--report", required=True, help="Markdown 报告路径")
    parser.add_argument("--ticker", default=None, help="目标 ticker（默认从报告/映射推断）")
    parser.add_argument("--pool", default=DEFAULT_POOL, help="pool.json 路径")
    parser.add_argument("--dry-run", action="store_true", help="只输出解析结果不写文件")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    if not os.path.exists(args.report):
        print(f"❌ 报告不存在: {args.report}")
        sys.exit(1)
    with open(args.report, encoding="utf-8") as f:
        text = f.read()

    info_grade = extract_info_grade(text)
    score = extract_score(text)
    scenarios = extract_scenarios(text)
    buy_zone = extract_buy_zone(text)
    cur_price = current_price_from_text(text)

    # ---- 联动 kelly_sizer ----
    kelly = None
    if scenarios:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from kelly_sizer import kelly_stats, apply_constraints

        # 计算收益率（需要当前价）
        for key, s in scenarios.items():
            if cur_price and cur_price > 0:
                s["return"] = (s["price"] - cur_price) / cur_price
            else:
                s["return"] = None

        if all(s.get("return") is not None for s in scenarios.values()):
            sc = [{"r": scenarios[k]["return"], "p": scenarios[k]["prob"]}
                  for k in ("bull", "base", "bear") if k in scenarios]
            stats = kelly_stats(sc)
            cons = apply_constraints(
                stats["f_half"] if stats["f_half"] is not None else None,
                score=score if score else 4.0,
                info_grade=info_grade if info_grade else "B",
            )
            kelly = {
                "mu": float(stats["mu"]),
                "var": float(stats["var"]),
                "f_full": float(stats["f_full"]) if stats["f_full"] is not None else None,
                "f_half": float(stats["f_half"]) if stats["f_half"] is not None else None,
                "final_position": float(cons["final_position"]),
                "recommendation": cons["recommendation"],
            }

    result = {
        "report": args.report,
        "info_grade": info_grade,
        "confidence_score": score,
        "current_price_from_report": cur_price,
        "scenarios": scenarios,
        "buy_zone": buy_zone,
        "kelly": kelly,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    # ---- 写入 pool.json ----
    if not args.dry_run:
        with open(args.pool, encoding="utf-8") as f:
            pool = json.load(f)

        ticker = args.ticker
        if not ticker:
            # 尝试从报告文件名推断（腾讯-research-... → 腾讯）
            base = os.path.basename(args.report)
            name_cn = re.split(r"[-_（(]", base)[0]
            for t, s in pool["stocks"].items():
                if s.get("name_cn", "").startswith(name_cn) or name_cn in s.get("name_cn", ""):
                    ticker = t
                    break
        if not ticker:
            print("⚠️ 无法推断 ticker，请用 --ticker 指定。未写入 pool.json。")
            if args.json:
                return
            sys.exit(2)

        if ticker not in pool["stocks"]:
            print(f"⚠️ ticker {ticker} 不在 pool.json 中，请先添加。未写入。")
            if args.json:
                return
            sys.exit(2)

        stock = pool["stocks"][ticker]
        if scenarios:
            stock["scenarios"] = {
                k: {"price": v["price"], "prob": v["prob"],
                    "return": v.get("return")}
                for k, v in scenarios.items()
            }
        if buy_zone:
            stock["buy_zone"] = {"low": buy_zone["low"], "high": buy_zone["high"]}
        if kelly:
            stock["kelly"] = kelly
        if score:
            stock["confidence_score"] = score
        if info_grade:
            stock["info_grade"] = info_grade
        stock["research_report"] = args.report
        if stock.get("status") is None:
            stock["status"] = "WATCHING"

        with open(args.pool, "w", encoding="utf-8") as f:
            json.dump(pool, f, ensure_ascii=False, indent=2)
        print(f"✅ 已写入 {ticker} → {args.pool}")
    else:
        print(f"\n[dry-run] 未写入。解析结果见上。")

    # ---- 人类可读摘要 ----
    print()
    print("=" * 60)
    print("解析摘要")
    print("=" * 60)
    print(f"  信息丰富度: {info_grade or '未找到'}")
    print(f"  综合评分:   {score if score else '未找到'}")
    if scenarios:
        for k, v in scenarios.items():
            print(f"  {k:>4}: 目标价 {v['price']:.2f}  概率 {v['prob']*100:.0f}%"
                  + (f"  收益率 {v['return']*100:+.1f}%" if v.get("return") is not None else ""))
    print(f"  击球区:     {buy_zone if buy_zone else '未找到'}")
    if kelly:
        print(f"  半凯利:     μ={kelly['mu']*100:+.1f}%  σ²={kelly['var']:.3f}  "
              f"f_half={kelly['f_half']:.2f} → 建议仓位 {kelly['final_position']*100:.1f}%")
    print("=" * 60)


if __name__ == "__main__":
    main()
