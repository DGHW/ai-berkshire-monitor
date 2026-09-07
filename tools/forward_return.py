#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
forward_return.py — 收益率期限结构确定性引擎（零依赖，仅标准库）

设计原则（配套 skills/forward-return.md）：
  LLM 只填假设（driver assumptions / probabilities / multiples / catalyst timing），
  Python 算全部数学——LLM 决定假设，deterministic engine 负责算。

数据：data/forecasts/{code}.json
命令：
  python tools/forward_return.py PDD --validate      # 校验 JSON schema + 概率
  python tools/forward_return.py PDD --horizon 2y    # 单期限详情
  python tools/forward_return.py PDD --all           # 全期限汇总表
"""
import argparse
import json
import math
import os
import sys

HORIZON_YEARS = {"6m": 0.5, "1y": 1.0, "2y": 2.0, "3y": 3.0, "5y": 5.0}
REQUIRED_FIELDS = ["code", "as_of", "snapshot_id", "price", "primary_horizon", "horizons"]
SCENARIO_ORDER = ["bull", "base", "bear", "extreme"]


def load_forecast(code, data_dir="data/forecasts"):
    path = os.path.join(data_dir, f"{code}.json")
    if not os.path.exists(path):
        sys.exit(f"[error] forecast file not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f), path


def validate(fc):
    """校验 schema 完整性 + 概率合法性。返回 error 列表。"""
    errors = []
    for k in REQUIRED_FIELDS:
        if k not in fc:
            errors.append(f"missing required field: {k}")
    if errors:
        return errors

    price = fc.get("price", 0)
    if not isinstance(price, (int, float)) or price <= 0:
        errors.append("price must be a positive number")

    ph = fc.get("primary_horizon", {})
    if ph.get("value") not in HORIZON_YEARS:
        errors.append(f"primary_horizon.value must be one of {list(HORIZON_YEARS)}")
    if not ph.get("selected_before_return_calculation"):
        errors.append("anti-overfit: primary_horizon.selected_before_return_calculation "
                      "must be true (先定期限再算收益，禁止看完曲线挑期限)")

    horizons = fc.get("horizons", {})
    for h, block in horizons.items():
        if h not in HORIZON_YEARS:
            errors.append(f"unknown horizon '{h}' (allowed: {list(HORIZON_YEARS)})")
            continue
        scenarios = block.get("scenarios", [])
        if not scenarios:
            errors.append(f"horizon {h}: no scenarios")
            continue
        total_p = 0.0
        for sc in scenarios:
            name = sc.get("name", "?")
            p = sc.get("probability")
            if not isinstance(p, (int, float)) or not (0 <= p <= 1):
                errors.append(f"horizon {h} / {name}: probability must be in [0,1], got {p}")
            else:
                total_p += p
            tv = sc.get("target_value")
            if not isinstance(tv, (int, float)) or tv <= 0:
                errors.append(f"horizon {h} / {name}: target_value must be positive, got {tv}")
        if abs(total_p - 1.0) > 1e-6:
            errors.append(f"horizon {h}: probabilities sum to {total_p:.4f}, expected 1.0")
    return errors


def horizon_stats(fc, horizon):
    """单期限：每 scenario 的 HPR/CAGR + 汇总统计。"""
    price = float(fc["price"])
    years = HORIZON_YEARS[horizon]
    scens = fc["horizons"][horizon]["scenarios"]
    rows = []
    exp_hpr = 0.0
    p_loss = 0.0
    p_loss30 = 0.0
    for sc in scens:
        name = sc.get("name", "?")
        prob = float(sc["probability"])
        tv = float(sc["target_value"])
        hpr = tv / price - 1.0
        cagr = (1.0 + hpr) ** (1.0 / years) - 1.0 if (1.0 + hpr) > 0 else -1.0
        rows.append({"name": name, "target_value": tv, "hpr": hpr,
                     "cagr": cagr, "probability": prob})
        exp_hpr += prob * hpr
        if hpr < 0:
            p_loss += prob
        if hpr < -0.30:
            p_loss30 += prob
    exp_cagr = (1.0 + exp_hpr) ** (1.0 / years) - 1.0 if (1.0 + exp_hpr) > 0 else -1.0
    return {"horizon": horizon, "years": years, "price": price, "rows": rows,
            "expected_hpr": exp_hpr, "expected_cagr": exp_cagr,
            "p_loss": p_loss, "p_loss30": p_loss30}


def fmt_pct(x):
    return f"{x * 100:+.1f}%"


def print_horizon(st):
    print(f"\n{st['horizon'].upper()}  ({st['years']}y)")
    print("-" * 44)
    order = {n: i for i, n in enumerate(SCENARIO_ORDER)}
    rows = sorted(st["rows"], key=lambda r: order.get(r["name"], 99))
    for r in rows:
        print(f"{r['name']:<10}{fmt_pct(r['hpr']):>9}   p={r['probability'] * 100:.0f}%")
    print("-" * 44)
    print(f"{'Expected HPR':<12}{fmt_pct(st['expected_hpr']):>9}")
    print(f"{'Expected CAGR':<12}{fmt_pct(st['expected_cagr']):>9}")
    print(f"{'P(loss)':<12}{st['p_loss'] * 100:>8.1f}%")
    print(f"{'P(loss>30%)':<12}{st['p_loss30'] * 100:>8.1f}%")


def primary_verdict(fc):
    ph = fc["primary_horizon"]["value"]
    st = horizon_stats(fc, ph)
    print("\n=== PRIMARY HORIZON VERDICT ===")
    print(f"horizon          : {ph} (selected before return calculation)")
    print(f"Expected CAGR    : {fmt_pct(st['expected_cagr'])}")
    print(f"Expected HPR     : {fmt_pct(st['expected_hpr'])}")
    print(f"P(loss)          : {st['p_loss'] * 100:.1f}%")
    bear = min((r["cagr"] for r in st["rows"]), default=None)
    extreme = min((r["hpr"] for r in st["rows"]), default=None)
    print(f"Bear CAGR        : {fmt_pct(bear) if bear is not None else 'n/a'}")
    print(f"Extreme downside : {fmt_pct(extreme) if extreme is not None else 'n/a'}")


def main():
    ap = argparse.ArgumentParser(description="Forward Return deterministic engine")
    ap.add_argument("code", help="stock code, e.g. PDD")
    ap.add_argument("--validate", action="store_true", help="validate forecast JSON only")
    ap.add_argument("--horizon", choices=list(HORIZON_YEARS), help="print single horizon detail")
    ap.add_argument("--all", action="store_true", help="print all horizons + primary verdict")
    ap.add_argument("--data-dir", default="data/forecasts", help="forecast JSON directory")
    args = ap.parse_args()

    fc, path = load_forecast(args.code, args.data_dir)

    errors = validate(fc)
    if errors:
        print(f"[validate] FAILED ({len(errors)} error(s)) in {path}:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print(f"[validate] OK: {path}")

    if args.validate:
        return
    if args.horizon:
        print_horizon(horizon_stats(fc, args.horizon))
        return
    # --all（默认）
    for h in ["6m", "1y", "2y", "3y", "5y"]:
        if h in fc.get("horizons", {}):
            print_horizon(horizon_stats(fc, h))
    primary_verdict(fc)


if __name__ == "__main__":
    main()
