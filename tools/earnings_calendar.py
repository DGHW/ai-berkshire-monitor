#!/usr/bin/env python3
"""earnings_calendar.py — 财报披露窗口日历（AI Berkshire 买卖闭环 v7 P1）。

扫描 reports/ 下报告中的"验证窗口"段落，提取财报类型，映射为 A 股法定披露窗口：
  中报   → 08-25 ~ 08-31
  年报   → 04-20 ~ 04-30
  一季报 → 04-20 ~ 04-30
  三季报 → 10-25 ~ 10-31
生成 data/monitor/earnings_calendar.json；--check 输出今天处于窗口内的股票，
batch1 每日检查，窗口内股票自动入队 lite 速评（轻量证伪），异常升级 full 重研。

用法：
    python3 tools/earnings_calendar.py --build     # 重建日历
    python3 tools/earnings_calendar.py --check     # 今天窗口内的股票
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(REPO_ROOT, "reports")
CALENDAR_FILE = os.path.join(REPO_ROOT, "data", "monitor", "earnings_calendar.json")

# 财报类型 → 披露窗口（月-日起始，月-日结束）
REPORT_WINDOWS = {
    "中报": ("08-25", "08-31"),
    "半年报": ("08-25", "08-31"),
    "年报": ("04-20", "04-30"),
    "一季报": ("04-20", "04-30"),
    "三季报": ("10-25", "10-31"),
}

# 报告内"验证窗口"段落的财报类型关键词
TYPE_PAT = re.compile(r"(中报|半年报|年报|一季报|三季报)")


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def extract_code_from_filename(fname: str):
    m = re.match(r"^(\d{6})", fname)
    if m:
        return m.group(1)
    m = re.match(r"^(.+?)_(\d{6})_", fname)
    if m:
        return m.group(2)
    return None


def build_calendar():
    """扫描报告，生成 {code: [{type, window_start, window_end, file}]}。"""
    calendar = {}
    files = os.listdir(REPORTS_DIR)
    for fname in files:
        if not fname.endswith(".md"):
            continue
        code = extract_code_from_filename(fname)
        if not code:
            continue
        path = os.path.join(REPORTS_DIR, fname)
        try:
            text = open(path, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        # 找"验证窗口"段落（其后 120 字符内找财报类型）
        for m in re.finditer(r"验证窗口[^\n]{0,120}", text):
            seg = m.group(0)
            for tm in TYPE_PAT.finditer(seg):
                rtype = tm.group(1)
                ws, we = REPORT_WINDOWS[rtype]
                entry = {"type": rtype, "window_start": ws, "window_end": we,
                         "file": fname, "context": seg[:80]}
                # 同 code 同类型去重
                existing = calendar.setdefault(code, [])
                if not any(e["type"] == rtype for e in existing):
                    existing.append(entry)
    # 过滤：只看未来窗口（含今天）
    today_md = datetime.now().strftime("%m-%d")
    for code in list(calendar.keys()):
        kept = [e for e in calendar[code] if e["window_end"] >= today_md]
        if kept:
            calendar[code] = kept
        else:
            del calendar[code]
    data = {"updated": datetime.now().strftime("%Y-%m-%d"),
            "windows": REPORT_WINDOWS,
            "calendar": calendar}
    os.makedirs(os.path.dirname(CALENDAR_FILE), exist_ok=True)
    with open(CALENDAR_FILE, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    total = sum(len(v) for v in calendar.values())
    print(f"✅ 财报日历已生成: {len(calendar)} 只股票 / {total} 个财报事件")
    return data


def check_today():
    """今天处于披露窗口内的股票清单。"""
    if not os.path.exists(CALENDAR_FILE):
        print(json.dumps({"ok": False, "error": "日历不存在，先运行 --build"}))
        return []
    data = json.load(open(CALENDAR_FILE, encoding="utf-8"))
    today_md = datetime.now().strftime("%m-%d")
    due = []
    for code, events in data.get("calendar", {}).items():
        for e in events:
            if e["window_start"] <= today_md <= e["window_end"]:
                due.append({"code": code, "type": e["type"], "file": e["file"]})
    result = {"date": datetime.now().strftime("%Y-%m-%d"), "due": due}
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return due


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="财报披露窗口日历")
    parser.add_argument("--build", action="store_true", help="重建日历（扫描报告）")
    parser.add_argument("--check", action="store_true", help="今天窗口内股票")
    args = parser.parse_args()
    if args.build:
        build_calendar()
    elif args.check:
        check_today()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
