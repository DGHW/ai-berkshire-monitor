#!/usr/bin/env python3
"""quote_fetcher.py — 统一行情接口（AI Berkshire 扩展 · v2）。

数据源策略（实测 2026-08-07，本机网络环境）：
  - 腾讯系（qt.gtimg.cn / web.ifzq.gtimg.cn）✅ 直连可用，主源
  - 新浪系（hq.sinajs.cn / quotes.sina.cn）    ✅ 直连可用，备源
  - 东方财富系（akshare 默认源）                ❌ 反爬断开，仅作理论兜底

实时快照：腾讯 → 新浪 降级
历史日线：腾讯(qfq前复权) → 新浪 降级

用法：
    python3 tools/quote_fetcher.py spot 600519           # A股实时快照
    python3 tools/quote_fetcher.py hist 600519 --days 120  # 日线历史(前复权)
    python3 tools/quote_fetcher.py spot-multi 600519 000858  # 批量快照
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

try:
    import requests
except ImportError:
    requests = None

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TICKER_MAP_FILE = os.path.join(REPO_ROOT, "data", "monitor", "ticker_map.json")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def to_cn_symbol(symbol: str) -> str:
    """统一市场符号路由（腾讯/新浪行情代码）。

    支持（实测 2026-08-07 全部直连可用）：
      A股   '600519' → 'sh600519'，'000858' → 'sz000858'，'920002' → 'bj920002'
      港股  '0700.HK' / '00700.HK' / 'hk00700' → 'hk00700'
      美股  'AAPL' / 'PDD' / 'usAAPL' → 'usAAPL'
      ETF   '510300' → 'sh510300'
    """
    s = symbol.strip().upper()

    # 显式前缀（hk/us/sh/sz/bj）直接使用
    low = s.lower()
    if low.startswith(("sh", "sz", "bj", "hk", "us")):
        return low

    # 港股：数字 + .HK / .H（腾讯标准为 hk+5位数字，不足补前导零）
    if re.search(r"^\d{4,5}\.(HK|H)$", s):
        code = s.split(".")[0]
        return f"hk{code:0>5}"

    # 美股：纯字母代码（非 A 股数字代码）
    if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", s):
        return f"us{s}"

    # A股数字代码
    if s.startswith(("4", "8")) or (s.startswith("92") and len(s) == 6):
        return f"bj{s}"
    if s.startswith(("6", "9")):
        return f"sh{s}"
    if s.startswith(("0", "2", "3")):
        return f"sz{s}"
    return f"sh{s}"  # 默认沪市


# ---------------------------------------------------------------------------
# 数据源 1: 腾讯实时快照（主）
# ---------------------------------------------------------------------------
def fetch_tencent_spot(symbol: str) -> dict | None:
    cn = to_cn_symbol(symbol)
    r = requests.get(f"https://qt.gtimg.cn/q={cn}", headers=UA, timeout=10)
    r.encoding = "gbk"
    m = re.search(r'v_{cn}="([^"]+)"'.replace("{cn}", cn), r.text)
    if not m:
        return None
    f = m.group(1).split("~")
    # 不同市场字段数不同：A股40+、港股78、北交所87，核心字段位置一致
    if len(f) < 35:
        return None
    try:
        price = float(f[3])
        prev_close = float(f[4])
    except (ValueError, IndexError):
        return None
    return {
        "symbol": symbol,
        "cn_symbol": cn,
        "name_cn": f[1],
        "price": price,
        "prev_close": prev_close,
        "open": float(f[5]) if f[5] else 0.0,
        "volume": float(f[6]) * 100 if f[6] else 0.0,  # 手 → 股
        "change": float(f[31]) if len(f) > 31 and f[31] else 0.0,
        "change_pct": float(f[32]) if len(f) > 32 and f[32] else 0.0,
        "high": float(f[33]) if len(f) > 33 and f[33] else 0.0,
        "low": float(f[34]) if len(f) > 34 and f[34] else 0.0,
        "amount": float(f[37]) * 10000 if len(f) > 37 and f[37] else 0.0,  # 万元 → 元
        "turnover": float(f[38]) if len(f) > 38 and f[38] else 0.0,  # 换手率%
        "pe_ttm": float(f[39]) if len(f) > 39 and f[39] else 0.0,
        "source": "tencent",
        "ts": datetime.now().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# 数据源 2: 新浪实时快照（备）
# ---------------------------------------------------------------------------
def fetch_sina_spot(symbol: str) -> dict | None:
    cn = to_cn_symbol(symbol)
    r = requests.get(f"https://hq.sinajs.cn/list={cn}",
                     headers={**UA, "Referer": "https://finance.sina.com.cn"}, timeout=10)
    r.encoding = "gbk"
    m = re.search(r'="([^"]+)"', r.text)
    if not m or not m.group(1).strip():
        return None
    f = m.group(1).split(",")
    if len(f) < 32:
        return None
    price = float(f[3])
    prev = float(f[2])
    return {
        "symbol": symbol,
        "cn_symbol": cn,
        "name_cn": f[0],
        "price": price,
        "prev_close": prev,
        "open": float(f[1]),
        "volume": float(f[8]),
        "change": price - prev,
        "change_pct": (price - prev) / prev * 100 if prev else 0.0,
        "high": float(f[4]),
        "low": float(f[5]),
        "amount": float(f[9]),
        "turnover": 0.0,
        "pe_ttm": 0.0,
        "source": "sina",
        "ts": datetime.now().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# 数据源 1: 腾讯历史日线（主，前复权）
# ---------------------------------------------------------------------------
def fetch_tencent_hist(symbol: str, days: int = 120) -> list[dict] | None:
    cn = to_cn_symbol(symbol)
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={cn},day,,,{days},qfq")
    r = requests.get(url, headers=UA, timeout=10)
    d = r.json()
    node = d.get("data", {}).get(cn, {})
    rows_raw = node.get("qfqday") or node.get("day")
    if not rows_raw:
        return None
    rows = []
    for item in rows_raw:
        # [日期, 开, 收, 高, 低, 成交量(手)]
        rows.append({
            "date": item[0],
            "open": float(item[1]),
            "close": float(item[2]),
            "high": float(item[3]),
            "low": float(item[4]),
            "volume": float(item[5]) * 100,  # 手 → 股
        })
    return rows


# ---------------------------------------------------------------------------
# 数据源 2: 新浪历史日线（备）
# ---------------------------------------------------------------------------
def fetch_sina_hist(symbol: str, days: int = 120) -> list[dict] | None:
    cn = to_cn_symbol(symbol)
    url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/"
           f"CN_MarketDataService.getKLineData?symbol={cn}&scale=240&ma=no&datalen={days}")
    r = requests.get(url, headers={**UA, "Referer": "https://finance.sina.com.cn"}, timeout=10)
    m = re.search(r"\((\[.*\])\)", r.text, re.S)
    if not m:
        return None
    arr = json.loads(m.group(1))
    if not arr:
        return None
    rows = []
    for item in arr:
        rows.append({
            "date": item["day"][:10],
            "open": float(item["open"]),
            "close": float(item["close"]),
            "high": float(item["high"]),
            "low": float(item["low"]),
            "volume": float(item["volume"]),
        })
    return rows


# ---------------------------------------------------------------------------
# 统一入口：降级链
# ---------------------------------------------------------------------------
def get_spot(symbol: str) -> dict:
    errs = []
    for fn, name in [(fetch_tencent_spot, "tencent"), (fetch_sina_spot, "sina")]:
        try:
            r = fn(symbol)
            if r:
                return r
            errs.append(f"{name}: 无数据")
        except Exception as e:
            errs.append(f"{name}: {e}")
    raise RuntimeError(f"所有数据源失败 [{symbol}]: {' | '.join(errs)}")


def get_hist(symbol: str, days: int = 120) -> list[dict]:
    errs = []
    for fn, name in [(fetch_tencent_hist, "tencent"), (fetch_sina_hist, "sina")]:
        try:
            r = fn(symbol, days)
            if r:
                return r
            errs.append(f"{name}: 无数据")
        except Exception as e:
            errs.append(f"{name}: {e}")
    raise RuntimeError(f"所有数据源失败 [{symbol}]: {' | '.join(errs)}")


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="统一行情接口（腾讯/新浪免费源）")
    sub = parser.add_subparsers(dest="cmd")

    p1 = sub.add_parser("spot", help="实时快照")
    p1.add_argument("symbol")
    p1.add_argument("--json", action="store_true")

    p2 = sub.add_parser("spot-multi", help="批量实时快照")
    p2.add_argument("symbols", nargs="+")
    p2.add_argument("--json", action="store_true")

    p3 = sub.add_parser("hist", help="日线历史(前复权)")
    p3.add_argument("symbol")
    p3.add_argument("--days", type=int, default=120)
    p3.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.cmd == "spot":
        r = get_spot(args.symbol)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print(f"{r['name_cn']} ({r['symbol']})  现价 {r['price']:.2f}  "
                  f"{r['change_pct']:+.2f}%  成交额 {r['amount']/1e8:.2f}亿  "
                  f"PE(TTM) {r['pe_ttm']:.1f}  [{r['source']}]")
    elif args.cmd == "spot-multi":
        results = []
        for s in args.symbols:
            try:
                results.append(get_spot(s))
            except Exception as e:
                results.append({"symbol": s, "error": str(e)})
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            for r in results:
                if "error" in r:
                    print(f"{r['symbol']:<10} ❌ {r['error']}")
                else:
                    print(f"{r['name_cn']} ({r['symbol']})  现价 {r['price']:.2f}  "
                          f"{r['change_pct']:+.2f}%  [{r['source']}]")
    elif args.cmd == "hist":
        rows = get_hist(args.symbol, args.days)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            latest = rows[-1]
            print(f"{args.symbol}  最近{len(rows)}日  最新: {latest['date']} 收盘 {latest['close']:.2f}  [{len(rows)} bars]")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
