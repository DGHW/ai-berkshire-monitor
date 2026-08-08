#!/usr/bin/env python3
"""financial_data.py — 财务数据接入层（Tushare + 关键兜底）。

解决"财报滞后"：财务数据从 Tushare Pro 一手拉取（财报日历、利润表、财务指标）。
本机东财系被反爬断开，akshare 兜底但不稳定。

用法：
    python3 tools/financial_data.py calendar 600519          # 财报日历
    python3 tools/financial_data.py indicator 600519          # 财务指标(ROE/毛利率等)
    python3 tools/financial_data.py income 600519            # 利润表
    python3 tools/financial_data.py balance 600519           # 资产负债表
    python3 tools/financial_data.py upcoming --days 30        # 监控池全部 next_earnings
    python3 tools/financial_data.py batch-update             # 批量刷新 pool.json 的 next_earnings

Token 从 data/api_keys.json 读取（已 gitignore）。亦可通过 TUSHARE_TOKEN 环境变量覆盖。
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_FILE = os.path.join(REPO_ROOT, "data", "api_keys.json")
POOL_FILE = os.path.join(REPO_ROOT, "data", "monitor", "pool.json")
TICKER_MAP_FILE = os.path.join(REPO_ROOT, "data", "monitor", "ticker_map.json")


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_config() -> dict:
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def load_token() -> str:
    token = os.environ.get("TUSHARE_TOKEN", "")
    if token:
        return token
    return load_config().get("tushare_token", "")


def get_pro():
    """延迟初始化 Tushare pro API（支持第三方代理端点）。"""
    import tushare as ts
    cfg = load_config()
    token = load_token()
    if not token:
        raise RuntimeError(f"未找到 Tushare token，请配置 {TOKEN_FILE}")
    ts.set_token(token)
    pro = ts.pro_api()
    # 第三方代理端点（如 ts.gyzcloud.top）
    api_url = cfg.get("tushare_api_url") or os.environ.get("TUSHARE_API_URL")
    if api_url:
        pro._DataApi__http_url = api_url
    return pro


def _to_tushare_code(symbol: str) -> str:
    """600519 → 600519.SH；0700.HK → 0700.HK；AAPL → AAPL.OQ（注：Pro 美股需不同接口）"""
    s = symbol.strip().upper()
    if s.endswith(".HK") or s.endswith(".SH") or s.endswith(".SZ") or s.endswith(".BJ"):
        return s
    if s.endswith(".US"):
        return s.replace(".US", ".OQ")
    if s.startswith(("6", "9")):
        return f"{s}.SH"
    if s.startswith(("0", "2", "3")):
        return f"{s}.SZ"
    if s.startswith(("4", "8")) or (s.startswith("92") and len(s) == 6):
        return f"{s}.BJ"
    return s


# ---------------------------------------------------------------------------
# 1. 财报日历：未来披露的财报日期
# ---------------------------------------------------------------------------
def fetch_calendar(ts_code: str, limit: int = 20) -> list[dict]:
    """财报日历。返回最近的披露记录（含未来的 pre_date 预约披露日）。"""
    pro = get_pro()
    df = pro.disclosure_date(ts_code=ts_code,
                             fields="ts_code,ann_date,end_date,pre_date,actual_date,modify_date",
                             limit=limit)
    if df.empty:
        return []
    records = df.to_dict("records")
    # 按 end_date 倒序，最新在前
    records.sort(key=lambda r: str(r.get("end_date", "")), reverse=True)
    return records


def fetch_upcoming(days: int = 30) -> list[dict]:
    """监控池 + ticker_map 全部标的的未来 N 日财报日历"""
    pro = get_pro()
    today = datetime.now().strftime("%Y%m%d")
    end_dt = (datetime.now() + timedelta(days=days)).strftime("%Y%m%d")
    # 按披露日期范围查询全部
    df = pro.disclosure_date(start_date=today, end_date=end_dt,
                             fields="ts_code,ann_date,end_date,pre_date,actual_date")
    if df.empty:
        return []
    records = df.to_dict("records")
    # 加载 mapping
    with open(TICKER_MAP_FILE, encoding="utf-8") as f:
        tmap = json.load(f)
    monitored_codes = set()
    for ticker in tmap:
        info = tmap[ticker]
        if isinstance(info, dict) and info.get("market") == "A":
            monitored_codes.add(_to_tushare_code(ticker))
    filtered = [r for r in records if r["ts_code"] in monitored_codes]
    return filtered


# ---------------------------------------------------------------------------
# 2. 利润表
# ---------------------------------------------------------------------------
def fetch_income(ts_code: str, limit: int = 4) -> list[dict]:
    pro = get_pro()
    df = pro.income(ts_code=ts_code, fields="ts_code,end_date,ann_date,revenue,oper_cost,operate_profit,total_profit,n_income,n_income_attr_parent,basic_eps",
                    limit=limit)
    return df.to_dict("records") if len(df) else []


# ---------------------------------------------------------------------------
# 3. 财务指标（ROE/毛利率/净利率/资产负债率）
# ---------------------------------------------------------------------------
def fetch_indicator(ts_code: str, limit: int = 8) -> list[dict]:
    pro = get_pro()
    # 注意：gross_margin 是毛利额（元），grossprofit_margin 才是毛利率（%）
    df = pro.fina_indicator(ts_code=ts_code,
                            fields="ts_code,end_date,ann_date,roe,grossprofit_margin,netprofit_margin,debt_to_assets,quick_ratio,op_yoy,or_yoy,eps,ocfps,fcff",
                            limit=limit)
    return df.to_dict("records") if len(df) else []


# ---------------------------------------------------------------------------
# 4. 资产负债表（关键科目）
# ---------------------------------------------------------------------------
def fetch_balance(ts_code: str, limit: int = 4) -> list[dict]:
    pro = get_pro()
    df = pro.balancesheet(ts_code=ts_code,
                          fields="ts_code,end_date,ann_date,total_assets,total_liab,total_hldr_eqy_exc_min_int,cash,short_term_loan,long_term_loan,inventories,goodwill",
                          limit=limit)
    return df.to_dict("records") if len(df) else []


# ---------------------------------------------------------------------------
# 5. 批量刷新 pool.json 的 next_earnings
# ---------------------------------------------------------------------------
def batch_update_next_earnings(days: int = 90) -> dict:
    """扫描 pool.json 全部 A 股标的，写入 next_earnings（即将披露的最新财报日期）"""
    with open(POOL_FILE, encoding="utf-8") as f:
        pool = json.load(f)
    if not load_token():
        return {"error": "no token", "updated": 0}
    updated = 0
    for ticker, stock in pool["stocks"].items():
        if stock.get("market") != "A":
            continue
        try:
            cal = fetch_calendar(_to_tushare_code(ticker))
            if cal:
                # 取最新的财报记录：pre_date（预约披露日）优先，其次 actual_date
                rec = cal[0]
                next_earnings = str(rec.get("pre_date") or rec.get("actual_date") or "")[:10]
                end_period = str(rec.get("end_date", ""))
                stock["next_earnings"] = next_earnings or None
                stock["next_earnings_period"] = end_period
                updated += 1
        except Exception as e:
            stock["next_earnings_error"] = str(e)
    with open(POOL_FILE, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)
    return {"updated": updated, "total": len(pool["stocks"])}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="财务数据接入（Tushare）")
    sub = parser.add_subparsers(dest="cmd")

    for name in ["calendar", "indicator", "income", "balance"]:
        p = sub.add_parser(name, help=f"按 ticker 拉{ name }")
        p.add_argument("ticker")
        p.add_argument("--limit", type=int, default=8)
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("upcoming", help="监控池全部未来 N 日财报")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("batch-update", help="批量更新 pool.json 的 next_earnings")
    p.add_argument("--days", type=int, default=90)

    args = parser.parse_args()
    if not load_token():
        print(f"❌ 未配置 Tushare token → {TOKEN_FILE}")
        sys.exit(1)

    if args.cmd == "calendar":
        data = fetch_calendar(_to_tushare_code(args.ticker), args.limit)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            for r in data[:10]:
                print(f"  {r.get('ts_code')} {r.get('end_date')} → {r.get('ann_date')} ({r.get('type','Q')})")
    elif args.cmd == "indicator":
        data = fetch_indicator(_to_tushare_code(args.ticker), args.limit)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            for r in data[:8]:
                print(f"  {r.get('end_date')} ROE {r.get('roe'):.2f}% 毛利率 {r.get('grossprofit_margin'):.2f}% 净利率 {r.get('netprofit_margin'):.2f}%")
    elif args.cmd == "income":
        data = fetch_income(_to_tushare_code(args.ticker), args.limit)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif args.cmd == "balance":
        data = fetch_balance(_to_tushare_code(args.ticker), args.limit)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif args.cmd == "upcoming":
        data = fetch_upcoming(args.days)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            for r in data:
                print(f"  {r.get('ts_code')} {r.get('end_date')} → 披露 {r.get('ann_date')} ({r.get('type','Q')})")
    elif args.cmd == "batch-update":
        result = batch_update_next_earnings(args.days)
        print(f"✅ 已更新 {result['updated']} 只 A 股的 next_earnings（共 {result.get('total','?')} 只）")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
