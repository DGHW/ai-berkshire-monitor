#!/usr/bin/env python3
"""Kelly Sizer — 半凯利仓位计算工具（AI Berkshire 扩展）。

从投资研究报告的三情景估值（乐观/中性/悲观）出发，
用连续版凯利公式计算建议仓位，并叠加硬性约束层。

公式：
    μ  = Σ p_i · r_i                        （期望收益）
    σ² = Σ p_i · (r_i − μ)²                 （方差）
    全凯利 f* = μ / σ²
    半凯利 f  = f* / 2

约束层（凯利给上限，不给目标）：
    1. 单票上限（小盘股更低）
    2. 报告综合评分折扣（<4.0 减半，<3.5 不建仓）
    3. 信息丰富度折扣（B 级 ×0.75，C 级 ×0.5）
    4. 流动性折扣（小盘股 ADTV 低 → ×0.5）
    5. 最终仓位 = min(半凯利, 各约束后的上限)

零外部依赖（仅 stdlib）。用法见 __main__。
"""

import argparse
import json
import sys
from decimal import Decimal, Context, ROUND_HALF_EVEN

_CTX = Context(prec=10, rounding=ROUND_HALF_EVEN)


def exact(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def kelly_stats(scenarios: list[dict]) -> dict:
    """从三情景计算期望收益 μ、方差 σ²、全凯利、半凯利。

    scenarios: [{"r": 收益率(小数), "p": 概率}, ...] 概率之和须 ≈ 1
    """
    rs = [exact(s["r"]) for s in scenarios]
    ps = [exact(s["p"]) for s in scenarios]

    total_p = sum(ps, Decimal("0"))
    if abs(total_p - 1) > Decimal("0.001"):
        raise ValueError(f"三情景概率之和应为 1，实际 {total_p}")

    mu = sum(p * r for p, r in zip(ps, rs))
    var = sum(p * (r - mu) ** 2 for p, r in zip(ps, rs))

    if var <= 0:
        return {"mu": mu, "var": var, "f_full": None, "f_half": None}

    f_full = mu / var
    f_half = f_full / 2
    return {
        "mu": mu,
        "var": var,
        "f_full": f_full,
        "f_half": f_half,
    }


def apply_constraints(
    f_half: Decimal,
    *,
    score: float,
    info_grade: str,
    adv_usd: float = None,
    is_small_cap: bool = False,
    max_position: float = 0.12,
    small_cap_max: float = 0.08,
) -> dict:
    """约束层：返回建议仓位与各约束明细。"""
    result = {}
    cap = Decimal(str(max_position if not is_small_cap else small_cap_max))

    # 1. 评分折扣
    if score < 3.5:
        result["score_verdict"] = "NO_BUY"
        result["score_reason"] = f"综合评分 {score} < 3.5，不建仓"
    elif score < 4.0:
        result["score_discount"] = Decimal("0.5")
        result["score_verdict"] = "DISCOUNT"
    else:
        result["score_discount"] = Decimal("1.0")
        result["score_verdict"] = "OK"

    # 2. 信息丰富度折扣
    grade_discount = {"A": Decimal("1.0"), "B": Decimal("0.75"), "C": Decimal("0.5")}
    result["grade_discount"] = grade_discount.get(info_grade.upper(), Decimal("0.5"))

    # 3. 流动性折扣（小盘股 ADTV < $1000万 → ×0.5）
    result["liquidity_discount"] = Decimal("1.0")
    if adv_usd is not None and adv_usd < 10_000_000:
        result["liquidity_discount"] = Decimal("0.5")

    # 4. 合成上限 = 单票上限 × 各折扣
    combined = cap
    for key in ("score_discount", "grade_discount", "liquidity_discount"):
        if key in result:
            combined *= result[key]
    result["combined_cap"] = combined

    # 5. 最终仓位 = min(半凯利, 合成上限)
    if result.get("score_verdict") == "NO_BUY" or f_half is None:
        result["final_position"] = Decimal("0")
        result["recommendation"] = "不建仓"
    else:
        final = min(f_half, combined)
        result["final_position"] = final
        if final <= 0:
            result["recommendation"] = "不建仓"
        elif final == combined:
            result["recommendation"] = "顶格仓位（受约束层限制）"
        else:
            result["recommendation"] = "半凯利仓位（未触上限）"
    return result


def main():
    parser = argparse.ArgumentParser(description="半凯利仓位计算")
    parser.add_argument("--scenarios", required=True,
                        help='JSON: [{"r":0.60,"p":0.25},{"r":0.25,"p":0.50},{"r":-0.30,"p":0.25}]')
    parser.add_argument("--score", type=float, default=4.0, help="报告综合评分 (1-5)")
    parser.add_argument("--info-grade", default="A", help="信息丰富度 A/B/C")
    parser.add_argument("--adv-usd", type=float, default=None, help="日均成交额(美元)")
    parser.add_argument("--small-cap", action="store_true", help="是否小盘股")
    parser.add_argument("--max-position", type=float, default=0.12, help="单票上限")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    scenarios = json.loads(args.scenarios)
    stats = kelly_stats(scenarios)
    cons = apply_constraints(
        stats["f_half"] if stats["f_half"] is not None else None,
        score=args.score, info_grade=args.info_grade,
        adv_usd=args.adv_usd, is_small_cap=args.small_cap,
        max_position=args.max_position,
    )

    if args.json:
        out = {**{k: str(v) for k, v in stats.items()},
               **{k: str(v) for k, v in cons.items()}}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    print("=" * 60)
    print("半凯利仓位计算 (Half-Kelly Position Sizing)")
    print("=" * 60)
    for i, s in enumerate(scenarios, 1):
        print(f"  情景{i}: 收益率 {float(s['r'])*100:+.1f}%  概率 {float(s['p'])*100:.0f}%")
    print(f"\n  期望收益 μ   = {float(stats['mu'])*100:+.2f}%")
    print(f"  方差 σ²      = {float(stats['var']):.4f}")
    if stats["f_full"] is not None:
        print(f"  全凯利 f*    = {float(stats['f_full']):.2f}")
        print(f"  半凯利 f     = {float(stats['f_half']):.2f}")
    print()
    print("  约束层:")
    print(f"    评分 {args.score:.1f} → {cons.get('score_verdict')}")
    print(f"    信息等级 {args.info_grade} → ×{cons.get('grade_discount')}")
    print(f"    流动性 → ×{cons.get('liquidity_discount')}")
    print(f"    合成上限 = {float(cons['combined_cap'])*100:.1f}%")
    print()
    print(f"  >>> 建议仓位: {float(cons['final_position'])*100:.1f}%  ({cons['recommendation']})")
    print("=" * 60)


if __name__ == "__main__":
    main()
