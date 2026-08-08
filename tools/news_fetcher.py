#!/usr/bin/env python3
"""news_fetcher.py — 多源新闻聚合接入（GDELT + 雪球 + 巨潮 + IR RSS）。

解决"新闻滞后"：替代"筠溪同志"等商业终端的实时资讯功能。
  - GDELT Project 全球新闻库（15 分钟更新，含中文财经媒体，免费）
  - 雪球大 V / 散户讨论（已有 tools/xueqiu_scraper.py）
  - 巨潮资讯 cninfo 公告流（A 股一手披露，免费）
  - 公司 IR RSS 邮件订阅 / RSS 链接

用法：
    python3 tools/news_fetcher.py gdelt 拼多多 --days 7 --json
    python3 tools/news_fetcher.py cninfo 600519.SH --days 30
    python3 tools/news_fetcher.py multi 600519 --days 7       # 聚合多源
    python3 tools/news_fetcher.py daily-digest                 # 生成监控池所有标的日报摘要

零依赖（仅 stdlib + requests）。其他源可后续扩展。
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from urllib.parse import quote

import requests

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_FILE = os.path.join(REPO_ROOT, "data", "api_keys.json")
POOL_FILE = os.path.join(REPO_ROOT, "data", "monitor", "pool.json")
TICKER_MAP_FILE = os.path.join(REPO_ROOT, "data", "monitor", "ticker_map.json")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _load_config():
    if not os.path.exists(TOKEN_FILE):
        return {}
    with open(TOKEN_FILE, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 1. GDELT Project 全球新闻（15 分钟更新，免费 API）
# ---------------------------------------------------------------------------
_GDELT_MIN_INTERVAL = 5.0  # GDELT 限流 5 秒/请求
_last_gdelt_call = [0.0]


def fetch_gdelt(query: str, days: int = 7, max_records: int = 50) -> list[dict]:
    """GDELT DOC 2.0 API — 关键词检索全球新闻（多语言，含中文财经媒体）。
    注意：GDELT 限流 5 秒/请求。本函数自动 sleep 限流。
    """
    import time
    cfg = _load_config()
    if not cfg.get("news_sources", {}).get("gdelt", True):
        return []
    max_records = cfg.get("gdelt_max_records", max_records)
    # 限流
    elapsed = time.time() - _last_gdelt_call[0]
    if elapsed < _GDELT_MIN_INTERVAL:
        time.sleep(_GDELT_MIN_INTERVAL - elapsed)
    end_dt = datetime.now().strftime("%Y%m%d%H%M%S")
    start_dt = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d%H%M%S")
    url = ("https://api.gdeltproject.org/api/v2/doc/doc"
           f"?query={quote(query)}"
           f"&mode=ArtList"
           f"&maxrecords={max_records}"
           f"&format=json"
           f"&startdatetime={start_dt}"
           f"&enddatetime={end_dt}"
           f"&sort=datedesc")
    try:
        r = requests.get(url, headers=UA, timeout=15)
        _last_gdelt_call[0] = time.time()
        if r.status_code != 200:
            import sys
            print(f"  [gdelt 限流/错误 {r.status_code}] {query}", file=sys.stderr)
            return []
        data = r.json()
        articles = data.get("articles", [])
        return [{
            "source": "gdelt",
            "title": a.get("title", ""),
            "url": a.get("url", ""),
            "date": a.get("seendate", ""),
            "domain": a.get("domain", ""),
            "language": a.get("language", ""),
            "tone": a.get("tone", 0.0),
        } for a in articles]
    except Exception as e:
        import sys
        print(f"  [gdelt 错误] {query}: {e}", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# 2. 巨潮资讯 cninfo A 股公告（一手披露）
# ---------------------------------------------------------------------------
_CNINFO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
}


def _cninfo_org_id(ts_code: str) -> str:
    """巨潮 orgId 格式：gssh0+6位（上交所）/ gssz0+6位（深交所）/ gsbj1+6位（北交所）。
    例：600519.SH → gssh0600519；000858.SZ → gssz0000858；920002.BJ → gsbj1920002"""
    plain = ts_code.split(".")[0]
    if ts_code.endswith(".SH"):
        return f"gssh0{plain}"
    if ts_code.endswith(".SZ"):
        return f"gssz0{plain}"
    if ts_code.endswith(".BJ"):
        return f"gsbj1{plain}"
    return f"gssh0{plain}"


def _cninfo_plate(ts_code: str) -> str:
    if ts_code.endswith(".SH"):
        return "sh"
    if ts_code.endswith(".SZ"):
        return "sz"
    if ts_code.endswith(".BJ"):
        return "bj"
    return "sh"


def fetch_cninfo(ts_code: str, days: int = 30) -> list[dict]:
    """巨潮资讯公告检索 API。
    ts_code: 600519.SH / 000858.SZ 格式
    """
    cfg = _load_config()
    if not cfg.get("news_sources", {}).get("cninfo", True):
        return []
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    plate = _cninfo_plate(ts_code)
    org_id = _cninfo_org_id(ts_code)
    code = ts_code.split(".")[0]
    data = (f"pageNum=1&pageSize=30&column={plate}&tabName=fulltext&plate={plate}"
            f"&stock={code},{org_id}&searchkey=&secid=&category=&trade="
            f"&seDate={start_date}~{end_date}&sortName=&sortType=&isHLtitle=true")
    # 重试 3 次（巨潮偶发超时）
    for attempt in range(3):
        try:
            r = requests.post("http://www.cninfo.com.cn/new/hisAnnouncement/query",
                              headers=_CNINFO_HEADERS, data=data, timeout=20)
            if r.status_code != 200:
                if attempt < 2:
                    import time as _t
                    _t.sleep(2)
                    continue
                return []
            anns = r.json().get("announcements") or []
            results = []
            for a in anns:
                ts_ms = a.get("announcementTime", 0)
                try:
                    time_str = datetime.utcfromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d") if ts_ms else ""
                except (ValueError, OSError, OverflowError):
                    time_str = ""
                title = a.get("announcementTitle", "").replace("<em>", "").replace("</em>", "")
                results.append({
                    "source": "cninfo",
                    "title": title,
                    "url": "http://static.cninfo.com.cn/" + a.get("adjunctUrl", ""),
                    "date": time_str,
                    "code": ts_code,
                    "type": a.get("announcementType", ""),
                })
            return results
        except Exception as e:
            if attempt < 2:
                import time as _t
                _t.sleep(2)
                continue
            import sys
            print(f"  [cninfo 错误] {ts_code}: {e}", file=sys.stderr)
            return []
    return []


# ---------------------------------------------------------------------------
# 3. 雪球（已有 xueqiu_scraper.py 的轻封装）
# ---------------------------------------------------------------------------
def fetch_xueqiu(user_id: str, days: int = 7, keywords: str = "") -> list[dict]:
    """雪球用户发言。user_id: 段永平 = 1247347556
    需要先登录态（xueqiu_scraper.py 已实现）
    """
    cfg = _load_config()
    if not cfg.get("news_sources", {}).get("xueqiu", True):
        return []
    try:
        from xueqiu_scraper import fetch_user_posts
        posts = fetch_user_posts(user_id, days=days)
        if keywords:
            kws = [k.strip() for k in keywords.split(",") if k.strip()]
            posts = [p for p in posts if any(k in p.get("text", "") for k in kws)]
        return [{
            "source": "xueqiu",
            "title": p.get("text", "")[:80],
            "url": p.get("url", ""),
            "date": p.get("date", ""),
            "user_id": user_id,
        } for p in posts]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 4. 公司 IR RSS（基础占位）
# ---------------------------------------------------------------------------
def fetch_ir_rss(symbol: str) -> list[dict]:
    """公司 IR 页 RSS 抓取（占位实现，需要 RSS URL 配置后才能用）。
    配置位置：data/api_keys.json 的 custom_quote_endpoints.{symbol}.ir_rss
    """
    cfg = _load_config()
    custom = cfg.get("custom_quote_endpoints", {}).get(symbol, {})
    rss_url = custom.get("ir_rss")
    if not rss_url:
        return []
    try:
        r = requests.get(rss_url, headers=UA, timeout=10)
        if r.status_code != 200:
            return []
        # 简单解析 RSS items（只取标题/链接/日期，不做完整 XML 解析）
        items = re.findall(r"<item>(.*?)</item>", r.text, re.S)
        return [{
            "source": "ir_rss",
            "title": re.search(r"<title>(.*?)</title>", i, re.S).group(1) if re.search(r"<title>", i) else "",
            "url": re.search(r"<link>(.*?)</link>", i, re.S).group(1) if re.search(r"<link>", i) else "",
            "date": re.search(r"<pubDate>(.*?)</pubDate>", i, re.S).group(1) if re.search(r"<pubDate>", i) else "",
        } for i in items[:20]]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 5. 聚合：单 ticker 多源汇总
# ---------------------------------------------------------------------------
NAME_REVERSE = {}  # 中文名 → ticker 缓存


def _build_name_reverse():
    global NAME_REVERSE
    if NAME_REVERSE:
        return
    if not os.path.exists(TICKER_MAP_FILE):
        return
    with open(TICKER_MAP_FILE, encoding="utf-8") as f:
        tmap = json.load(f)
    for ticker, info in tmap.items():
        if not isinstance(info, dict):
            continue
        name = info.get("name_cn", "")
        if name:
            NAME_REVERSE[name] = ticker


def fetch_multi(symbol: str, days: int = 7) -> dict:
    """聚合多源新闻。"""
    _build_name_reverse()
    tmap = {}
    if os.path.exists(TICKER_MAP_FILE):
        with open(TICKER_MAP_FILE, encoding="utf-8") as f:
            tmap = json.load(f)
    info = tmap.get(symbol, {})
    name_cn = info.get("name_cn", symbol)

    results = {"symbol": symbol, "name_cn": name_cn, "sources": {}}

    # GDELT
    results["sources"]["gdelt"] = fetch_gdelt(name_cn, days=days)

    # 巨潮（仅 A 股）
    if info.get("market") == "A":
        ts_code = symbol if "." in symbol else f"{symbol}.SH"
        results["sources"]["cninfo"] = fetch_cninfo(ts_code, days=days)

    # 雪球（如果有 user_id 配置）
    dyp_id = _load_config().get("xueqiu_user_ids", {}).get("段永平")
    if dyp_id:
        results["sources"]["xueqiu_dyp"] = fetch_xueqiu(dyp_id, days=days, keywords=name_cn)

    return results


# ---------------------------------------------------------------------------
# 6. 监控池每日新闻摘要（供 price_monitor 报告调用）
# ---------------------------------------------------------------------------
def daily_digest() -> dict:
    """对监控池每只股票取近 7 日新闻摘要，返回 dict{ticker: [news_items]}"""
    with open(POOL_FILE, encoding="utf-8") as f:
        pool = json.load(f)
    digest = {}
    for ticker, stock in pool["stocks"].items():
        name_cn = stock.get("name_cn", "")
        try:
            news = fetch_gdelt(name_cn, days=7, max_records=8)
            if stock.get("market") == "A":
                ts_code = ticker if "." in ticker else f"{ticker}.SH"
                anns = fetch_cninfo(ts_code, days=14)
                digest[ticker] = {"name": name_cn, "news": news[:5], "announcements": anns[:5]}
            else:
                digest[ticker] = {"name": name_cn, "news": news[:5], "announcements": []}
        except Exception:
            digest[ticker] = {"name": name_cn, "news": [], "announcements": []}
    return digest


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="多源新闻聚合")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("gdelt", help="GDELT 关键词检索")
    p.add_argument("query")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--max", type=int, default=50)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("cninfo", help="巨潮公告")
    p.add_argument("ts_code")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("xueqiu", help="雪球用户发言")
    p.add_argument("user_id")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--keywords", default="")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("multi", help="单 ticker 多源聚合")
    p.add_argument("symbol")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--json", action="store_true")

    sub.add_parser("daily-digest", help="监控池每日新闻摘要")

    args = parser.parse_args()

    if args.cmd == "gdelt":
        data = fetch_gdelt(args.query, args.days, args.max)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            for a in data:
                print(f"  [{a['date']}] {a['title'][:100]}  ({a['domain']})")
    elif args.cmd == "cninfo":
        data = fetch_cninfo(args.ts_code, args.days)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            for a in data:
                print(f"  [{a['date']}] {a['title'][:80]}")
    elif args.cmd == "xueqiu":
        data = fetch_xueqiu(args.user_id, args.days, args.keywords)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            for p in data:
                print(f"  [{p['date']}] {p['title'][:80]}")
    elif args.cmd == "multi":
        data = fetch_multi(args.symbol, args.days)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(f"\n=== {data['name_cn']} ({data['symbol']}) 多源新闻摘要 ===")
            for src, items in data["sources"].items():
                print(f"\n[{src}] {len(items)} 条")
                for it in items[:5]:
                    print(f"  [{it['date']}] {it['title'][:80]}")
    elif args.cmd == "daily-digest":
        digest = daily_digest()
        print(json.dumps(digest, ensure_ascii=False, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
