#!/usr/bin/env python3
"""pool_kelly.py — 中位数偏差凯利仓位工具（AI Berkshire · v4 仓位层）。

逻辑（用户确认版）：
    候选池 = 买入组 + 观察组（所有留下研究的股票）
    G_med = 池内内在涨幅中位数                    （基准锚，月度冻结）
    δᵢ   = Gᵢ − G_med                             （个股相对池中位数的超额）
    fᵢ*  = δᵢ / σ²_pool                            （连续凯利，池方差做分母）
    fᵢ   = 半凯利 = fᵢ*/2，clamp 到 [0, 单票上限]

含义：跑赢池中位数的股票拿更大仓位，跑输的自动缩小甚至为零
      —— 仓位 = 该股在池内相对吸引力的函数，横向竞争排序。

基准冻结：月度轮动时重算并写入 data/positions/kelly_basis.json，
          月度内买入统一用冻结基准（避免池构成变动导致仓位漂移）。

用法：
    python3 tools/pool_kelly.py 600036            # 算单只建议仓位
    python3 tools/pool_kelly.py --refresh-basis   # 重算并冻结基准（月度轮动时跑）
    python3 tools/pool_kelly.py --list            # 列出池内所有股票建议仓位
"""

import argparse
import json
import os
import statistics
import sys
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GROUPS_FILE = os.path.join(REPO_ROOT, "data", "monitor", "portfolio_groups.json")
BASIS_FILE = os.path.join(REPO_ROOT, "data", "positions", "kelly_basis.json")

MAX_POSITION = 0.12        # 单票上限 12%
SMALL_CAP_MAX = 0.08       # 小盘股上限 8%
FLOOR_RATIO = 0.0          # 超额为负 → 0 仓位（不买）


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


def get_pool_gains(groups) -> list:
    """候选池 = 买入组+观察组，返回 [(code, gain_med), ...] 仅取有涨幅中位数的。"""
    out = []
    for g in ("buy", "watch"):
        for c, s in groups.get(g, {}).items():
            if s.get("gain_med") is not None:
                out.append((c, float(s["gain_med"])))
    return out


def pool_stats(groups) -> dict:
    """计算池基准：中位涨幅 G_med + 池方差 σ²。"""
    gains = [g for _, g in get_pool_gains(groups)]
    if len(gains) < 5:
        return {"G_med": 0.0, "sigma2": 0.01, "n": len(gains), "frozen": False}
    G_med = statistics.median(gains)
    var = statistics.pvariance(gains) if len(gains) > 1 else 0.01
    if var <= 0:
        var = 0.01
    return {"G_med": G_med, "sigma2": var, "n": len(gains), "frozen": False}


def load_basis() -> dict:
    """加载冻结基准；不存在则实时计算。"""
    basis = load_json(BASIS_FILE)
    return basis


def kelly_for(code: str, groups, basis) -> dict:
    """单只建议仓位。"""
    gain = None
    for g in ("buy", "watch", "reserve"):
        if code in groups.get(g, {}):
            gain = groups[g][code].get("gain_med")
            group = g
            break
    if gain is None:
        return {"code": code, "error": "该股票不在候选池或缺少涨幅中位数数据"}

    G_med = basis.get("G_med", 0.0)
    sigma2 = basis.get("sigma2", 0.01)
    delta = float(gain) - G_med

    # 单位换算：涨幅是百分数（如 7.5 表示 7.5%），凯利需用小数（0.075）
    delta_dec = delta / 100.0
    sigma2_dec = sigma2 / 10000.0

    # 连续凯利 f* = δ/σ²；半凯利 /2
    f_full = delta_dec / sigma2_dec if sigma2_dec > 0 else 0
    f_half = f_full / 2.0

    # 负超额 → 0 仓位（不买）
    if f_half <= 0:
        f_final = FLOOR_RATIO
        note = f"超额内在涨幅 {delta:+.1f}% ≤ 0（池中位 {G_med}%），不建仓"
    else:
        # clamp 到单票上限
        is_small = code.startswith(("920", "8", "4"))
        cap = SMALL_CAP_MAX if is_small else MAX_POSITION
        f_final = min(f_half, cap)
        note = f"超额内在涨幅 {delta:+.1f}%（池中位 {G_med}%），半凯利 {f_half*100:.1f}%"

    return {
        "code": code,
        "gain_med": gain,
        "pool_G_med": G_med,
        "delta": round(delta, 1),
        "sigma2": round(sigma2, 3),
        "f_full": round(f_full, 4),
        "f_half": round(f_half, 4),
        "f_final": round(f_final, 4),
        "suggested_pct": round(f_final * 100, 1),
        "note": note,
    }


def refresh_basis(groups) -> dict:
    """重算并冻结基准（月度轮动时调用）。"""
    stats = pool_stats(groups)
    basis = {
        "G_med": stats["G_med"],
        "sigma2": stats["sigma2"],
        "n": stats["n"],
        "frozen_at": datetime.now().strftime("%Y-%m-%d"),
        "pool_size": len(groups.get("buy", {})) + len(groups.get("watch", {})),
    }
    save_json(BASIS_FILE, basis)
    return basis


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="中位数偏差凯利仓位工具")
    parser.add_argument("code", nargs="?", default=None, help="股票代码")
    parser.add_argument("--refresh-basis", action="store_true", help="重算并冻结池基准（月度）")
    parser.add_argument("--list", action="store_true", help="列出候选池全部建议仓位")
    args = parser.parse_args()

    groups = load_json(GROUPS_FILE)

    if args.refresh_basis:
        basis = refresh_basis(groups)
        print(f"✅ 基准已冻结: G_med={basis['G_med']}% σ²={basis['sigma2']} "
              f"样本={basis['n']} 冻结日={basis['frozen_at']}")
        print(f"   候选池 {basis['pool_size']} 只，下次月度轮动时再刷新")
        return

    basis = load_basis()
    if not basis:
        basis = pool_stats(groups)
        print(f"⚠️ 使用实时基准（未冻结）: G_med={basis['G_med']}% σ²={basis['sigma2']}")
    else:
        print(f"📌 使用冻结基准（{basis.get('frozen_at','?')}）: "
              f"G_med={basis['G_med']}% σ²={basis['sigma2']}")

    if args.code:
        r = kelly_for(args.code, groups, basis)
        print(f"\n{'='*50}")
        print(f"股票 {r.get('code')}:")
        for k, v in r.items():
            if k != "code":
                print(f"  {k}: {v}")
        print(f"{'='*50}")
        print(f"\n👉 建议仓位: {r.get('suggested_pct', 0)}%")
        if r.get("f_final", 0) <= 0:
            print("   （该股跑输池中位数，暂不建仓）")
        return

    if args.list:
        print(f"\n{'代码':<8}{'名称':<8}{'涨幅':>7}{'超额':>8}{'建议仓位':>9}")
        print("-" * 45)
        for c, s in groups.get("buy", {}).items():
            r = kelly_for(c, groups, basis)
            if "error" in r: continue
            print(f"{c:<8}{s['name']:<8}{r['gain_med']:>7.1f}{r['delta']:>+8.1f}"
                  f"{r['suggested_pct']:>8.1f}%")
        for c, s in groups.get("watch", {}).items():
            r = kelly_for(c, groups, basis)
            if "error" in r: continue
            print(f"{c:<8}{s['name']:<8}{r['gain_med']:>7.1f}{r['delta']:>+8.1f}"
                  f"{r['suggested_pct']:>8.1f}%")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
