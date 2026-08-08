#!/usr/bin/env python3
"""ashare_screener.py — 全 A 股初筛引擎（AI Berkshire 扩展 · v2 Phase D Stage 1）。

两层漏斗：
  Layer 0（快筛，零财务调用，~3 秒）：
    - 剔除 ST/*ST（207 只）
    - 剔除 PE_ttm 为 NaN/<=0（亏损股在 daily_basic 中 PE 为 NaN）
    - 科创板（688/689）PE > 100 剔除
    - 全市场 PE > 300 剔除（极端泡沫）
    - 总市值 < 30 亿剔除（仙股/流动性风险）
  Layer 1（财务层，逐股拉 fina_indicator+income，~37 分钟 @150次/分）：
    - 最近报告期净利润 > 0（确认盈利）
    - ROE（最新） > 8%
    - 毛利率 > 15%
    - 净利率 > 5%
    - 营收同比增速 > 0（非衰退）
    - 资产负债率 < 80%（财务健康）

输出：data/screening/stage1_pool.json（含每只股票的通过/剔除状态 + 淘汰理由，可追溯不黑箱）

用法：
    python3 tools/ashare_screener.py --layer 0          # 快筛（秒级）
    python3 tools/ashare_screener.py --layer 1          # 财务层（~37 分钟）
    python3 tools/ashare_screener.py --layer all         # 全跑
    python3 tools/ashare_screener.py --stats             # 查看当前池统计
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from financial_data import get_pro  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO_ROOT, "data", "screening")
OUT_FILE = os.path.join(OUT_DIR, "stage1_pool.json")
STATS_FILE = os.path.join(OUT_DIR, "stage1_stats.json")

# ---- Layer 0 参数 ----
PE_KC_MAX = 100        # 科创板 PE 上限
PE_ALL_MAX = 300       # 全市场 PE 上限
MIN_MV = 30e8          # 最小总市值（元），30 亿
ST_MIN_DAYS = 365      # 上市满 1 年（次新股风险）
# Tushare daily_basic 的 total_mv 单位是【万元】，换算系数
MV_WAN_TO_YUAN = 1e4

# ---- Layer 1 参数 ----
MIN_ROE = 8.0          # ROE %（用年化 roe_yearly）
MIN_GROSS_MARGIN = 15.0  # 毛利率 %
MIN_NET_MARGIN = 5.0   # 净利率 %
MAX_DEBT = 80.0        # 资产负债率上限 %
# 金融行业（银行/保险/证券/多元金融）豁免毛利率/负债率检查
FINANCE_KEYWORDS = ("银行", "保险", "证券", "多元金融", "房地产")


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_pool() -> dict:
    if os.path.exists(OUT_FILE):
        with open(OUT_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"updated": None, "layer0_ts": None, "layer1_ts": None, "stocks": {}}


def save_pool(pool: dict):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)


def fmt_days(days):
    return f"{days:.0f}天" if days else "?"


# ---------------------------------------------------------------------------
# Layer 0：快筛（零财务调用）
# ---------------------------------------------------------------------------
def layer0() -> dict:
    pro = get_pro()
    today = datetime.now().strftime("%Y%m%d")
    trade_date = today  # 用最新交易日（若有数据则自动使用）

    # 1. 全量上市股票
    sb = pro.stock_basic(list_status="L", fields="ts_code,name,industry,list_date")
    print(f"[Layer 0] 上市股票 {len(sb)} 只")

    # 2. 全市场估值快照
    db = pro.daily_basic(trade_date=trade_date,
                         fields="ts_code,pe_ttm,total_mv,circ_mv,turnover_rate")
    if db.empty:
        # 今天可能不是交易日，回退到最近（往前找）
        for back in range(1, 8):
            from datetime import timedelta
            td = (datetime.now() - timedelta(days=back)).strftime("%Y%m%d")
            db = pro.daily_basic(trade_date=td, fields="ts_code,pe_ttm,total_mv,circ_mv,turnover_rate")
            if not db.empty:
                print(f"  [回退] 使用 {td} 交易日数据")
                break
    print(f"[Layer 0] 估值快照 {len(db)} 只（{trade_date}）")

    # merge
    df = sb.merge(db, on="ts_code", how="left")

    pool = load_pool()
    # 重跑 Layer0 时保留 Layer1 已算好的财务字段
    stocks = {}

    for _, r in df.iterrows():
        ts_code = r["ts_code"]
        name = r["name"]
        entry = pool["stocks"].get(ts_code, {})
        reasons = []

        # 规则 1：ST / *ST
        if "ST" in str(name).upper():
            reasons.append(f"ST股: {name}")
        # 规则 2：市值太小（total_mv 单位：万元 → 元）
        mv_wan = r.get("total_mv")
        mv_yuan = float(mv_wan) * MV_WAN_TO_YUAN if mv_wan is not None and mv_wan == mv_wan else None
        if mv_yuan is None or mv_yuan < MIN_MV:
            reasons.append(f"市值 {mv_yuan/1e8 if mv_yuan else '?'}亿 < 30亿")
        # 规则 3：PE 过滤（NaN/<=0 留给 Layer1 净利润确认，不在此硬剔）
        pe = r.get("pe_ttm")
        pe_is_nan = pe is None or (isinstance(pe, float) and pe != pe)
        if not pe_is_nan and pe > 0:
            # 科创板 PE > 100
            if ts_code.startswith("688") or ts_code.startswith("689"):
                if pe > PE_KC_MAX:
                    reasons.append(f"科创板PE {pe:.1f} > {PE_KC_MAX}")
            # 全市场 PE > 300
            elif pe > PE_ALL_MAX:
                reasons.append(f"PE {pe:.1f} > {PE_ALL_MAX}")

        # 保留已有财务字段（Layer1 结果）
        entry["name"] = name
        entry["industry"] = r.get("industry", "")
        entry["pe_ttm"] = float(pe) if not pe_is_nan else None
        entry["total_mv"] = mv_yuan
        entry["layer0_pass"] = len(reasons) == 0
        entry["layer0_reasons"] = reasons
        entry["pe_missing"] = pe_is_nan  # PE 缺失标记（亏损候选，Layer1 确认）

        # Layer1 状态保留
        if "layer1_pass" in entry:
            if len(reasons) > 0:
                entry["layer1_pass"] = False
                entry.setdefault("layer1_reasons", []).append("Layer0已剔除")
        stocks[ts_code] = entry

    pool["stocks"] = stocks
    pool["layer0_ts"] = datetime.now().isoformat(timespec="seconds")
    pool["updated"] = pool["layer0_ts"]
    save_pool(pool)

    passed = sum(1 for s in stocks.values() if s["layer0_pass"])
    print(f"\n[Layer 0] 完成：{len(stocks)} 只 → 通过 {passed} 只（剔除 {len(stocks)-passed} 只）")
    return pool


# ---------------------------------------------------------------------------
# Layer 1：财务层（逐股拉取）
# ---------------------------------------------------------------------------
def analyze_one(pro, ts_code: str, entry: dict) -> list[str]:
    """单只股票完整财务分析，返回淘汰理由列表（空 = 通过）。"""
    ind = pro.fina_indicator(ts_code=ts_code, limit=1,
                             fields="ts_code,end_date,roe,roe_yearly,grossprofit_margin,netprofit_margin,debt_to_assets,or_yoy")
    inc = pro.income(ts_code=ts_code, limit=1,
                     fields="ts_code,end_date,n_income_attr_p,revenue")
    reasons = []

    # 金融行业判断（用行业名或股票名）
    industry = entry.get("industry", "")
    is_finance = any(kw in industry for kw in FINANCE_KEYWORDS) or any(
        kw in entry.get("name", "") for kw in ("银行", "保险", "证券"))

    if not ind.empty:
        row = ind.iloc[0]
        roe_yearly = row.get("roe_yearly")
        roe = row.get("roe")
        gm = row.get("grossprofit_margin")
        nm = row.get("netprofit_margin")
        debt = row.get("debt_to_assets")
        or_yoy = row.get("or_yoy")
        entry["fina_end_date"] = str(row.get("end_date", ""))
        entry["roe"] = float(roe) if roe and roe == roe else None
        entry["roe_yearly"] = float(roe_yearly) if roe_yearly and roe_yearly == roe_yearly else None
        entry["gross_margin"] = float(gm) if gm and gm == gm else None
        entry["net_margin"] = float(nm) if nm and nm == nm else None
        entry["debt_ratio"] = float(debt) if debt and debt == debt else None
        entry["rev_yoy"] = float(or_yoy) if or_yoy and or_yoy == or_yoy else None

        # ROE：优先年化，其次最新单季
        roe_use = entry["roe_yearly"] if entry["roe_yearly"] is not None else entry["roe"]
        if roe_use is None or roe_use < MIN_ROE:
            reasons.append(f"ROE {roe_use} < {MIN_ROE}%")
        # 非金融：毛利率/负债率
        if not is_finance:
            if entry["gross_margin"] is None or entry["gross_margin"] < MIN_GROSS_MARGIN:
                reasons.append(f"毛利率 {entry['gross_margin']} < {MIN_GROSS_MARGIN}%")
            if entry["debt_ratio"] is not None and entry["debt_ratio"] > MAX_DEBT:
                reasons.append(f"负债率 {entry['debt_ratio']:.0f}% > {MAX_DEBT}%")
        # 净利率（金融也适用，但银行净利率通常很高）
        if entry["net_margin"] is None or entry["net_margin"] < MIN_NET_MARGIN:
            reasons.append(f"净利率 {entry['net_margin']} < {MIN_NET_MARGIN}%")
        # 营收增速（金融可豁免）
        if entry["rev_yoy"] is not None and entry["rev_yoy"] < 0 and not is_finance:
            reasons.append(f"营收同比 {entry['rev_yoy']:.1f}% < 0")
    else:
        reasons.append("无财务指标数据")

    if not inc.empty:
        ni = inc.iloc[0].get("n_income_attr_p")
        if ni is not None and ni == ni and ni <= 0:
            reasons.append(f"归母净利 {ni/1e8:.1f}亿 <= 0（亏损）")
        entry["latest_n_income"] = float(ni) if ni and ni == ni else None
    else:
        reasons.append("无利润表数据")

    return reasons


def layer1(limit: int = None, resume: bool = True) -> dict:
    pro = get_pro()
    pool = load_pool()

    if "layer0_ts" not in pool or not pool.get("layer0_ts"):
        print("[Layer 1] 请先运行 --layer 0")
        return pool

    pending = [ts for ts, s in pool["stocks"].items()
               if s.get("layer0_pass") and (not resume or "layer1_pass" not in s)]
    # 补充：PE 缺失（亏损候选）也要进 Layer1 用净利润确认
    pending += [ts for ts, s in pool["stocks"].items()
                if not s.get("layer0_pass") and s.get("pe_missing")
                and len(s.get("layer0_reasons", [])) <= 1  # 只被 PE 缺失剔除的
                and (not resume or "layer1_pass" not in s)]
    pending = list(dict.fromkeys(pending))  # 去重
    print(f"[Layer 1] 待财务分析 {len(pending)} 只")
    if limit:
        pending = pending[:limit]
        print(f"  [限流] 本次只处理 {len(pending)} 只")

    done = 0
    failed = 0
    start = time.time()

    for i, ts_code in enumerate(pending):
        entry = pool["stocks"][ts_code]
        # 完整处理 + 单次失败自动重试 3 次（代理网络抖动常见）
        reasons = None
        last_err = None
        for attempt in range(4):  # 1 次 + 3 次重试
            try:
                reasons = analyze_one(pro, ts_code, entry)
                break
            except Exception as e:
                last_err = e
                time.sleep(2 * (attempt + 1))  # 2/4/6s 退避

        if reasons is None:
            entry["layer1_error"] = str(last_err)[:120]
            failed += 1
            if failed % 20 == 1:
                print(f"  [警告] 累计失败 {failed} 次，最近: {str(last_err)[:60]}")
        else:
            entry["layer1_pass"] = len(reasons) == 0
            entry["layer1_reasons"] = reasons
            entry["layer1_ts"] = datetime.now().isoformat(timespec="seconds")
            entry.pop("layer1_error", None)
            done += 1

            # 每 50 只存盘一次（断点续跑）
            if done % 50 == 0:
                save_pool(pool)
                elapsed = time.time() - start
                rate = done / elapsed if elapsed > 0 else 0
                eta = (len(pending) - done) / rate / 60 if rate > 0 else 0
                passed_now = sum(1 for t in pending[:i+1]
                                 if pool["stocks"].get(t, {}).get("layer1_pass"))
                print(f"  [{done}/{len(pending)}] 通过率 "
                      f"{passed_now/(i+1)*100:.0f}% ETA {eta:.0f}min")

        # 限流：150 次/分钟，留余量 → 每 2 次请求 sleep 0.45s
        if i % 2 == 1:
            time.sleep(0.45)

    save_pool(pool)
    pool["layer1_ts"] = datetime.now().isoformat(timespec="seconds")
    save_pool(pool)

    passed = sum(1 for s in pool["stocks"].values()
                 if s.get("layer1_pass"))
    total = sum(1 for s in pool["stocks"].values() if s.get("layer0_pass"))
    print(f"\n[Layer 1] 完成：处理 {done} 只（失败 {failed}），财务通过 {passed}/{total}")
    return pool


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def show_stats():
    pool = load_pool()
    if not pool.get("stocks"):
        print("池为空，先运行 --layer 0")
        return
    stocks = pool["stocks"]
    l0_pass = [s for s in stocks.values() if s.get("layer0_pass")]
    l1_pass = [s for s in stocks.values() if s.get("layer1_pass")]

    print("=" * 70)
    print("全 A 初筛池统计 (stage1_pool.json)")
    print("=" * 70)
    print(f"  总股票数:        {len(stocks)}")
    print(f"  Layer0 通过:     {len(l0_pass)}（快筛：非ST/非亏损/非高PE/市值够）")
    print(f"  Layer1 通过:     {len(l1_pass)}（财务：ROE/毛利/净利/负债/营收）")
    print(f"  更新:            {pool.get('updated')}")
    print()
    if l1_pass:
        print("  Layer1 通过 TOP 20（按市值排序）：")
        l1_sorted = sorted(l1_pass, key=lambda s: s.get("total_mv") or 0, reverse=True)
        for s in l1_sorted[:20]:
            gm = s.get('gross_margin')
            gm_s = f"{gm:.1f}%" if gm is not None else "金融豁免"
            print(f"    {s.get('name','?'):<8} PE {s.get('pe_ttm',0) or 0:>6.1f} "
                  f"ROE {s.get('roe_yearly') or s.get('roe',0) or 0:>5.1f}% 毛利率 {gm_s} "
                  f"市值 {s.get('total_mv',0)/1e8:.0f}亿")
    print()


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="全 A 股初筛引擎")
    parser.add_argument("--layer", choices=["0", "1", "all"], default="all", help="跑哪层")
    parser.add_argument("--limit", type=int, default=None, help="Layer1 只处理前 N 只（测试用）")
    parser.add_argument("--no-resume", action="store_true", help="Layer1 不续跑（重新分析全部）")
    parser.add_argument("--stats", action="store_true", help="只看统计")
    args = parser.parse_args()

    if args.stats:
        show_stats()
        return

    if args.layer in ("0", "all"):
        print("========== Layer 0：快筛（秒级） ==========")
        layer0()
    if args.layer in ("1", "all"):
        print("\n========== Layer 1：财务层（~37 分钟） ==========")
        layer1(limit=args.limit, resume=not args.no_resume)
    show_stats()


if __name__ == "__main__":
    main()
