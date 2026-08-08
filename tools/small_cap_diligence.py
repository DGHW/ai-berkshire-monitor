#!/usr/bin/env python3
"""small_cap_diligence.py — 小盘股冷信息挖掘工具（AI Berkshire 扩展 · C级标的专用）。

设计理念：小盘股没有讨论热度（无批量研报/媒体/散户讨论），所以"搜更多新闻"没用。
本工具从【不需要讨论热度】的信息源挖掘：
  1. 财务时间序列异常（应收/存货/在建工程/资本开支 环比突变 = 业务拐点信号）
  2. 股东结构变化（股东户数变化、十大流通股东进出 = 主力动向）
  3. 大宗交易/龙虎榜（机构席位 = 聪明钱）
  4. 公告分类挖掘（增持/回购/质押/诉讼/合同中标 等事件型信号）
  5. 产业链交叉（上游涨价/下游需求信号，可选）

数据源：Tushare Pro（financial_data.py 的 get_pro）+ 巨潮公告（news_fetcher 复用）

用法：
    python3 tools/small_cap_diligence.py 002272 --json      # 全量冷信息挖掘
    python3 tools/small_cap_diligence.py 002272 --signal    # 只看信号摘要
    python3 tools/small_cap_diligence.py 002272 --out md    # 输出 Markdown 供 skill 引用
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from financial_data import get_pro, _to_tushare_code  # noqa: E402
from news_fetcher import fetch_cninfo  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def fnum(v, nd=2):
    """格式化数值，None/NaN → '-'"""
    if v is None:
        return "-"
    try:
        if v != v:  # NaN
            return "-"
        return f"{v:,.{nd}f}"
    except (ValueError, TypeError):
        return "-"


def retry_call(fn, *args, retries=3, backoff=2.0, **kwargs):
    """Tushare 代理网络抖动重试。"""
    import time as _t
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if attempt < retries - 1:
                _t.sleep(backoff * (attempt + 1))
            else:
                raise e


# ---------------------------------------------------------------------------
# 1. 财务时间序列异常（季度环比突变）
# ---------------------------------------------------------------------------
def detect_financial_anomalies(ts_code: str) -> dict:
    """拉近 8 期利润表+资产负债表，检测应收/存货/在建工程/资本开支的环比突变。"""
    pro = get_pro()
    result = {"periods": [], "signals": []}

    try:
        inc = retry_call(pro.income, ts_code=ts_code,
                         fields="ts_code,end_date,revenue,n_income_attr_p,operate_profit,total_profit",
                         limit=8)
        bal = retry_call(pro.balancesheet, ts_code=ts_code,
                               fields="ts_code,end_date,total_assets,total_liab,inventories,accounts_receiv,fix_assets,const_in_progress,total_cur_assets,total_cur_liab,monetary_fund",
                               limit=8)
        cash = retry_call(pro.cashflow, ts_code=ts_code,
                            fields="ts_code,end_date,n_cashflow_act,capital_expend",
                            limit=8)

        # 按 end_date 合并
        periods = {}
        for _, r in inc.iterrows():
            d = str(r.get("end_date", ""))
            periods.setdefault(d, {})["revenue"] = r.get("revenue")
            periods[d]["n_income"] = r.get("n_income_attr_p")
            periods[d]["op_profit"] = r.get("operate_profit")
        for _, r in bal.iterrows():
            d = str(r.get("end_date", ""))
            periods.setdefault(d, {})["inventories"] = r.get("inventories")
            periods[d]["ar"] = r.get("accounts_receiv")
            periods[d]["cip"] = r.get("const_in_progress")  # 在建工程
            periods[d]["total_assets"] = r.get("total_assets")
            periods[d]["monetary"] = r.get("monetary_fund")
        for _, r in cash.iterrows():
            d = str(r.get("end_date", ""))
            periods.setdefault(d, {})["ocf"] = r.get("n_cashflow_act")
            periods[d]["capex"] = r.get("capital_expend")

        sorted_dates = sorted(periods.keys(), reverse=True)
        result["periods"] = [{"end_date": d, **periods[d]} for d in sorted_dates[:8]]

        # 环比突变检测（最新期 vs 上一期）
        if len(sorted_dates) >= 2:
            cur, prev = periods[sorted_dates[0]], periods[sorted_dates[1]]

            # ① 盈利质量信号：营收/净利/营业利润/经营现金流 趋势（亏损股最重要）
            for key, label in [("revenue", "营收"), ("n_income", "净利润"),
                               ("op_profit", "营业利润"), ("ocf", "经营现金流")]:
                a, b = cur.get(key), prev.get(key)
                if a is None or b is None or b == 0:
                    continue
                chg = (a - b) / abs(b)
                if a < 0:
                    if chg > 0.1:
                        result["signals"].append({"type": key, "label": label, "change_pct": round(chg*100,1),
                                                  "note": f"{label}环比改善 {fnum(chg*100,1)}%（但仍为负）"})
                    else:
                        result["signals"].append({"type": key, "label": label, "change_pct": round(chg*100,1),
                                                  "note": f"{label}为负 {fnum(a/1e8,2)}亿（亏损中）"})
                elif chg < -0.20:
                    result["signals"].append({"type": key, "label": label, "change_pct": round(chg*100,1),
                                              "note": f"{label}环比下滑 {fnum(chg*100,1)}%"})

            # ② 资产质量信号：应收/存货/在建 突变
            for key, label, thresh in [
                ("inventories", "存货", 0.30),
                ("ar", "应收账款", 0.30),
                ("cip", "在建工程", 0.30),
                ("capex", "资本开支", 0.40),
            ]:
                a, b = cur.get(key), prev.get(key)
                if a and b and b != 0:
                    chg = (a - b) / abs(b)
                    if abs(chg) > thresh:
                        direction = "激增" if chg > 0 else "骤降"
                        result["signals"].append({
                            "type": key, "label": label, "change_pct": round(chg * 100, 1),
                            "note": f"{label}{direction} {fnum(chg*100,1)}% "
                                    f"({fnum(prev.get(key))} → {fnum(cur.get(key))} 元)",
                        })

            # ③ 应收/营收 比率（收入质量）
            if cur.get("ar") and cur.get("revenue") and cur["revenue"] != 0:
                ar_ratio = cur["ar"] / cur["revenue"]
                result["ar_to_revenue"] = round(ar_ratio, 2)
                if ar_ratio > 1.0:
                    result["signals"].append({"type": "ar_quality", "label": "应收/营收",
                                              "note": f"应收账款是营收的 {fnum(ar_ratio,2)} 倍（收入质量差，回款风险）"})

            # ④ 经营现金流/净利润（利润质量）
            if cur.get("ocf") and cur.get("n_income") and cur["n_income"] != 0:
                result["ocf_to_ni"] = round(cur["ocf"] / cur["n_income"], 2)

            # 货币资金 vs 存货
            if cur.get("monetary") and cur.get("inventories"):
                result["cash_to_inv"] = round(cur["monetary"] / cur["inventories"], 2) if cur["inventories"] else None
    except Exception as e:
        result["error"] = str(e)[:120]
    return result


# ---------------------------------------------------------------------------
# 2. 股东结构变化
# ---------------------------------------------------------------------------
def detect_holder_changes(ts_code: str) -> dict:
    """股东户数变化 + 十大流通股东变动。"""
    pro = get_pro()
    result = {"holder_trend": [], "top10": [], "signals": []}

    try:
        # 股东户数（stk_holdernumber）
        try:
            hn = retry_call(pro.stk_holdernumber, ts_code=ts_code, fields="ts_code,end_date,holder_num,holder_num_change", limit=6)
            for _, r in hn.iterrows():
                result["holder_trend"].append({
                    "end_date": str(r.get("end_date", "")),
                    "holders": r.get("holder_num"),
                    "change_pct": r.get("holder_num_change"),
                })
            if len(result["holder_trend"]) >= 2:
                latest = result["holder_trend"][0]
                # 与最近期比较（短期波动）
                prev = result["holder_trend"][1]
                if latest["holders"] and prev["holders"] and prev["holders"] != 0:
                    chg = (latest["holders"] - prev["holders"]) / prev["holders"] * 100
                    if abs(chg) > 3:
                        note = "股东户数" + ("减少（筹码集中）" if chg < 0 else "增加（筹码分散）")
                        result["signals"].append({"type": "holders", "note": f"{note} {fnum(chg,1)}%（近一期）"})
                # 与最早比较（中期趋势，20-30 天窗口）
                if len(result["holder_trend"]) >= 3:
                    base = result["holder_trend"][-1]
                    if latest["holders"] and base["holders"] and base["holders"] != 0:
                        chg_mid = (latest["holders"] - base["holders"]) / base["holders"] * 100
                        if abs(chg_mid) > 5:
                            note = "股东户数中期" + ("减少（筹码集中）" if chg_mid < 0 else "增加（筹码分散）")
                            result["signals"].append({"type": "holders", "note": f"{note} {fnum(chg_mid,1)}%（多期）"})
        except Exception:
            pass  # 无权限接口跳过

        # 十大流通股东（top10_holders）
        try:
            t10 = retry_call(pro.top10_holders, ts_code=ts_code, fields="ts_code,end_date,holder_name,hold_ratio", limit=10)
            for _, r in t10.iterrows():
                result["top10"].append({
                    "end_date": str(r.get("end_date", "")),
                    "name": str(r.get("holder_name", ""))[:30],
                    "ratio": r.get("hold_ratio"),
                })
        except Exception:
            pass
    except Exception as e:
        result["error"] = str(e)[:120]
    return result


# ---------------------------------------------------------------------------
# 3. 大宗交易 / 龙虎榜
# ---------------------------------------------------------------------------
def detect_trades(ts_code: str, days: int = 90) -> dict:
    """大宗交易 + 龙虎榜（机构/游资席位）。"""
    pro = get_pro()
    result = {"block_trades": [], "lhb": [], "signals": []}
    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    today = datetime.now().strftime("%Y%m%d")

    try:
        bt = retry_call(pro.block_trade, ts_code=ts_code, start_date=start, end_date=today,
                             fields="ts_code,trade_date,price,vol,amount,buyer_name,seller_name")
        for _, r in bt.head(10).iterrows():
            result["block_trades"].append({
                "date": str(r.get("trade_date", "")),
                "price": r.get("price"),
                "amount": r.get("amount"),
                "buyer": str(r.get("buyer_name", ""))[:25],
                "seller": str(r.get("seller_name", ""))[:25],
            })
        if len(result["block_trades"]) >= 3:
            result["signals"].append({"type": "block_trade",
                                      "note": f"近{days}天 {len(result['block_trades'])} 笔大宗交易"})
    except Exception:
        pass

    try:
        lhb = retry_call(pro.top_list, ts_code=ts_code, start_date=start, end_date=today,
                           fields="ts_code,trade_date,name,close,amount,buy_amount,sell_amount,net_amount,reason")
        for _, r in lhb.head(10).iterrows():
            result["lhb"].append({
                "date": str(r.get("trade_date", "")),
                "reason": str(r.get("reason", ""))[:40],
                "net_amount": r.get("net_amount"),
            })
        if result["lhb"]:
            result["signals"].append({"type": "lhb", "note": f"近{days}天 {len(result['lhb'])} 次上龙虎榜"})
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# 4. 公告分类挖掘（巨潮）
# ---------------------------------------------------------------------------
SIGNAL_KEYWORDS = {
    "增持": ["增持", "股东增持"],
    "回购": ["回购"],
    "中标": ["中标", "合同", "订单"],
    "质押": ["质押"],
    "减持": ["减持", "股东减持"],
    "诉讼": ["诉讼", "仲裁"],
    "担保": ["对外担保"],
    "限售解禁": ["限售", "解禁"],
    "股权激励": ["股权激励", "限制性股票"],
    "业绩预增": ["业绩预告", "预增", "预盈"],
    "业绩预减": ["预减", "预亏", "业绩下滑"],
    "重组": ["重组", "收购", "并购", "股权转让"],
    "人事": ["董事长", "总经理", "辞职", "换届"],
    "投资扩产": ["投资", "扩建", "产能", "项目"],
}


def classify_announcements(ts_code: str, days: int = 180) -> dict:
    """拉巨潮公告并按信号类型分类。"""
    result = {"total": 0, "by_type": {}, "signals": []}
    try:
        anns = fetch_cninfo(ts_code, days=days)
        result["total"] = len(anns)
        matched_types = set()
        for a in anns:
            title = a.get("title", "")
            for cat, kws in SIGNAL_KEYWORDS.items():
                if any(k in title for k in kws):
                    result["by_type"].setdefault(cat, []).append({
                        "date": a.get("date", ""), "title": title[:60],
                    })
                    matched_types.add(cat)
        for cat in matched_types:
            items = result["by_type"][cat]
            result["signals"].append({
                "type": cat, "note": f"近{days}天 {len(items)} 条{capt}公告",
            })
    except Exception as e:
        result["error"] = str(e)[:120]
    return result


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def full_diligence(symbol: str, days: int = 90) -> dict:
    ts_code = _to_tushare_code(symbol)
    return {
        "symbol": symbol,
        "ts_code": ts_code,
        "financial_anomalies": detect_financial_anomalies(ts_code),
        "holder_changes": detect_holder_changes(ts_code),
        "trades": detect_trades(ts_code, days),
        "announcements": classify_announcements(ts_code, days),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def render_md(data: dict) -> str:
    """渲染为 Markdown（供 small-cap-diligence skill 引用）。"""
    lines = [f"# 小盘股冷信息挖掘：{data['symbol']} ({data['ts_code']})", ""]
    lines.append(f"> 生成时间：{data['generated_at']} ｜ 数据源：Tushare + 巨潮")
    lines.append("")

    # 财务异常
    fa = data["financial_anomalies"]
    lines.append("## 一、财务时间序列异常")
    if fa.get("signals"):
        for s in fa["signals"]:
            lines.append(f"- ⚠️ **{s['label']}**：{s['note']}")
    else:
        lines.append("- 未检测到显著环比突变（>30% 阈值）")
    lines.append("")

    # 股东
    hc = data["holder_changes"]
    lines.append("## 二、股东结构")
    if hc.get("signals"):
        for s in hc["signals"]:
            lines.append(f"- ⚠️ {s['note']}")
    if hc.get("holder_trend"):
        lines.append("")
        lines.append("| 报告期 | 股东户数 | 变化% |")
        lines.append("|--------|---------|-------|")
        for h in hc["holder_trend"][:4]:
            lines.append(f"| {h['end_date']} | {fnum(h['holders'],0)} | {fnum(h['change_pct'],1)} |")
    lines.append("")

    # 交易
    tr = data["trades"]
    lines.append("## 三、大宗交易 / 龙虎榜")
    if tr.get("signals"):
        for s in tr["signals"]:
            lines.append(f"- ⚠️ {s['note']}")
    if tr.get("block_trades"):
        lines.append("")
        lines.append("| 日期 | 价格 | 金额(万) | 买方 | 卖方 |")
        lines.append("|------|------|---------|------|------|")
        for t in tr["block_trades"][:5]:
            amt = t.get("amount")
            lines.append(f"| {t['date']} | {fnum(t.get('price'),2)} | "
                         f"{fnum(amt/10000 if amt else None,0)} | {t['buyer']} | {t['seller']} |")
    lines.append("")

    # 公告
    ann = data["announcements"]
    lines.append(f"## 四、公告信号（近 180 天共 {ann.get('total', 0)} 条）")
    if ann.get("by_type"):
        for cat, items in ann["by_type"].items():
            lines.append(f"### {cat}（{len(items)} 条）")
            for it in items[:3]:
                lines.append(f"- [{it['date']}] {it['title']}")
    else:
        lines.append("- 无信号类公告")
    lines.append("")
    return "\n".join(lines)


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="小盘股冷信息挖掘")
    parser.add_argument("symbol", help="股票代码，如 002272")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--signal", action="store_true", help="只看信号摘要")
    parser.add_argument("--out", choices=["md", "text"], default="text")
    args = parser.parse_args()

    data = full_diligence(args.symbol, args.days)

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if args.out == "md":
        print(render_md(data))
        return

    # text 摘要
    print(f"\n=== 小盘股冷信息挖掘：{args.symbol} ({data['ts_code']}) ===")
    fa = data["financial_anomalies"]
    print("\n[财务异常]")
    for s in fa.get("signals", []):
        print(f"  ⚠️ {s['note']}")
    if not fa.get("signals"):
        print("  无显著突变")
    hc = data["holder_changes"]
    print("\n[股东结构]")
    for s in hc.get("signals", []):
        print(f"  ⚠️ {s['note']}")
    tr = data["trades"]
    print("\n[交易信号]")
    for s in tr.get("signals", []):
        print(f"  ⚠️ {s['note']}")
    ann = data["announcements"]
    print(f"\n[公告] 共 {ann.get('total',0)} 条")
    for cat, items in ann.get("by_type", {}).items():
        print(f"  · {cat}: {len(items)} 条")


if __name__ == "__main__":
    main()
