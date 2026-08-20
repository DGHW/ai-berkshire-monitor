#!/usr/bin/env python3
"""report_sync.py — 报告量化结论回填工具（AI Berkshire 买卖闭环 v6）。

解析 reports/ 下四视角研究报告的 `## 量化结论` 五字段，聚合为每只股票的
gain_med / entry_med / entry_min / zones / zone_all_red，写回：
  - data/monitor/portfolio_groups.json（买/观/后备/放弃 分组条目）
  - data/monitor/pool.json（buy_zone / entry_price / thesis_file）
不改组归属（组移动归 pool_rotator --monthly-rotate）。

用法：
    python3 tools/report_sync.py --code 000001 [--dry-run]   # 单只
    python3 tools/report_sync.py --all [--dry-run]           # 全量
    python3 tools/report_sync.py --list-missing              # 缺报告/解析失败
    python3 tools/report_sync.py --code 000001 --show        # 打印聚合结果不写
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from statistics import median

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(REPO_ROOT, "reports")
GROUPS_FILE = os.path.join(REPO_ROOT, "data", "monitor", "portfolio_groups.json")
POOL_FILE = os.path.join(REPO_ROOT, "data", "monitor", "pool.json")

# 视角 → 人物映射（文件名与内容共用）
VIEW_PERSON = {"商业模式": "段永平", "财务估值": "巴菲特", "行业竞争": "芒格", "风险评估": "李录"}

# ---------------------------------------------------------------------------
# 文件名正则（两种格式 + 可选日期后缀）
# ---------------------------------------------------------------------------
DASH_RE = re.compile(
    r"^(\d{6})(.+?)-(商业模式|财务估值|行业竞争|风险评估)-(段永平|巴菲特|芒格|李录)视角(?:-\d{8})?\.md$"
)
USCORE_RE = re.compile(
    r"^(.+?)_(\d{6})_(商业模式|财务估值|行业竞争|风险评估)_(段永平|巴菲特|芒格|李录)视角(?:[_-]\d{8})?\.md$"
)
SYNTH_RE = re.compile(r"^(\d{6})(.+?)-综合研判(?:-\d{8})?\.md$")

# ---------------------------------------------------------------------------
# 量化结论五字段（`## 量化结论` 小节内、锚定行首、兼容 ** 包裹与括号污染）
# 兼容三种变体：**字段名**: **值** / **字段名**: 值 / **字段名: 值**
# ---------------------------------------------------------------------------
GAIN_RE = re.compile(
    r"^[-*]\s*\**\s*内在涨幅\**\s*[:：]\s*\**\s*"
    r"(?:约\s*)?([+-]?\d+(?:\.\d+)?)\s*%"
    r"(?:\s*[~～至到-]\s*(?:约\s*)?([+-]?\d+(?:\.\d+)?)\s*%)?"
)
ZONE_RE = re.compile(r"^[-*]\s*\**\s*击球区\**\s*[:：]\s*\**\s*(🟢|🟡|🔴)")
ENTRY_RE = re.compile(r"^[-*]\s*\**\s*目标建仓价\**\s*[:：]\s*\**\s*(\d+(?:\.\d+)?)\s*元")
SECOND_RE = re.compile(r"^[-*]\s*\**\s*二次补仓价\**\s*[:：]\s*\**\s*(\d+(?:\.\d+)?)\s*元")
VERIFY_RE = re.compile(r"^[-*]\s*\**\s*数据核验\**\s*[:：]\s*\**\s*(✓|✅|⚠️|❌)")


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def parse_report(path: str):
    """解析单份报告。返回 {gain, zone, entry, second, verify_ok} 或 None。"""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    # 只取 "## 量化结论" 之后的小节
    m = re.search(r"^##\s*量化结论.*?$", text, re.M)
    if not m:
        return None
    section = text[m.end():]
    # 遇到下一个二级标题即截止
    section = re.split(r"^##\s", section, maxsplit=1, flags=re.M)[0]

    gain = ENTRY = second = None
    zone = None
    verify_ok = None
    for line in section.splitlines():
        line = line.strip()
        if gain is None:
            gm = GAIN_RE.match(line)
            if gm:
                gain = float(gm.group(1))
                if gm.group(2):
                    gain = (gain + float(gm.group(2))) / 2  # 区间取中值
                continue
        if zone is None:
            zm = ZONE_RE.match(line)
            if zm:
                zone = zm.group(1)
                continue
        if ENTRY is None:
            em = ENTRY_RE.match(line)
            if em:
                ENTRY = float(em.group(1))
                continue
        if second is None:
            sm = SECOND_RE.match(line)
            if sm:
                second = float(sm.group(1))
                continue
        if verify_ok is None:
            vm = VERIFY_RE.match(line)
            if vm:
                verify_ok = vm.group(1) in ("✓", "✅")
                continue
        if gain is not None and zone is not None and ENTRY is not None and second is not None and verify_ok is not None:
            break

    if gain is None or zone is None or ENTRY is None:
        return None
    return {"gain": gain, "zone": zone, "entry": ENTRY, "second": second, "verify_ok": verify_ok}


def find_reports(code: str):
    """返回该代码的 {视角: 文件路径} 映射（在 REPORTS_DIR 顶层）。

    同视角存在新旧多份文件（带 -YYYYMMDD 后缀）时，取 mtime 最新的一份
    （修复：此前依赖 os.listdir 顺序，新旧文件选择不确定）。
    """
    found = {}
    synth = None
    try:
        names = os.listdir(REPORTS_DIR)
    except OSError:
        return {}, None
    for name in names:
        if not name.endswith(".md"):
            continue
        m = DASH_RE.match(name)
        if m and m.group(1) == code:
            _set_newest(found, m.group(3), os.path.join(REPORTS_DIR, name))
            continue
        m = USCORE_RE.match(name)
        if m and m.group(2) == code:
            _set_newest(found, m.group(3), os.path.join(REPORTS_DIR, name))
            continue
        m = SYNTH_RE.match(name)
        if m and m.group(1) == code and synth is None:
            synth = os.path.join(REPORTS_DIR, name)
    return found, synth


def _set_newest(found: dict, view: str, path: str):
    """同视角取 mtime 最新的文件路径。"""
    old = found.get(view)
    if old is None:
        found[view] = path
        return
    try:
        if os.path.getmtime(path) > os.path.getmtime(old):
            found[view] = path
    except OSError:
        pass


def parse_stock(code: str):
    """聚合四视角 → {gain_med, entry_med, entry_min, zones, zone_all_red, thesis_file, parsed_count, errors}。"""
    reports, synth = find_reports(code)
    parsed = []
    errors = []
    for view in ("商业模式", "财务估值", "行业竞争", "风险评估"):
        path = reports.get(view)
        if not path:
            errors.append(f"缺 {view} 视角报告")
            continue
        r = parse_report(path)
        if r is None:
            errors.append(f"{view} 视角量化结论解析失败")
        else:
            parsed.append(r)
    if len(parsed) < 2:
        return None
    gains = [r["gain"] for r in parsed]
    entries = [r["entry"] for r in parsed if r["entry"] is not None]
    zones = [r["zone"] for r in parsed]
    return {
        "gain_med": median(gains),
        "entry_med": median(entries) if entries else None,
        "entry_min": min(entries) if entries else None,
        "zones": zones,
        "zone_all_red": zones.count("🔴") >= 3,
        "thesis_file": synth or next(iter(reports.values()), None),
        "parsed_count": len(parsed),
        "errors": errors,
        "verify_oks": [r["verify_ok"] for r in parsed if r["verify_ok"] is not None],
    }


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def _find_group(groups, code):
    for g in ("buy", "watch", "reserve", "drop"):
        if code in groups[g]:
            return g
    return None


def sync_stock(code: str, dry_run: bool):
    """解析并回填单只。返回 {"code","ok","group",...} 或错误信息。"""
    stock = parse_stock(code)
    if stock is None:
        return {"code": code, "ok": False, "errors": stock["errors"] if stock else ["四视角报告不足 2 份可解析"]}

    groups = load_json(GROUPS_FILE)
    pool = load_json(POOL_FILE)

    group = _find_group(groups, code)
    if group is None:
        # 不在任何组：归观察组（调用方可能后续轮动归类）
        group = "watch"
        groups[group].setdefault(code, {})
    entry = groups[group].setdefault(code, {})

    name = entry.get("name")
    if name in (None, "?"):
        # 尝试从报告文件名提取名称
        reports, _ = find_reports(code)
        name = "?" 
        for view, path in reports.items():
            base = os.path.basename(path)
            m = DASH_RE.match(base)
            if m and m.group(1) == code:
                name = m.group(2)
                break
            m = USCORE_RE.match(base)
            if m and m.group(2) == code:
                name = m.group(1)
                break
        entry["name"] = name

    price = entry.get("price")
    # 尝试用 pool 里的最新价
    pstock = pool["stocks"].get(code, {})
    if price in (None, 0) and pstock.get("last_price"):
        price = pstock["last_price"]

    new_fields = {
        "entry_med": stock["entry_med"],
        "entry_min": stock["entry_min"],
        "gain_med": stock["gain_med"],
        "zones": stock["zones"],
        "zone_all_red": stock["zone_all_red"],
    }
    if price is not None:
        new_fields["price"] = price
    entry.update(new_fields)

    # 同步 pool.json
    if code in pool["stocks"]:
        p = pool["stocks"][code]
        em = stock["entry_med"]
        p["entry_price"] = em
        p["buy_zone"] = {"low": round(em * 0.85, 2), "high": em}
        if stock["thesis_file"]:
            p["thesis_file"] = stock["thesis_file"]
        if name != "?" and p.get("name_cn") in (None, "?"):
            p["name_cn"] = name

    if not dry_run:
        groups["updated"] = datetime.now().strftime("%Y-%m-%d")
        save_json(GROUPS_FILE, groups)
        pool["updated"] = datetime.now().strftime("%Y-%m-%d")
        save_json(POOL_FILE, pool)

    return {
        "code": code, "ok": True, "group": group, "name": entry["name"],
        "gain_med": stock["gain_med"], "entry_med": stock["entry_med"],
        "entry_min": stock["entry_min"], "zones": stock["zones"],
        "zone_all_red": stock["zone_all_red"], "parsed": stock["parsed_count"],
        "errors": stock["errors"], "dry_run": dry_run,
    }


def sync_all(dry_run: bool):
    groups = load_json(GROUPS_FILE)
    codes = []
    for g in ("buy", "watch", "reserve", "drop"):
        codes.extend(groups[g].keys())
    results = []
    missing = []
    for code in sorted(set(codes)):
        r = sync_stock(code, dry_run=dry_run)
        results.append(r)
        if not r["ok"]:
            missing.append((code, r["errors"]))
    ok = sum(1 for r in results if r["ok"])
    print(f"全量回填：成功 {ok} / {len(results)}（dry_run={dry_run}）")
    for code, errs in missing:
        print(f"  ❌ {code}: {'; '.join(errs)}")
    return results


def list_missing():
    groups = load_json(GROUPS_FILE)
    missing = []
    for g in ("buy", "watch", "reserve", "drop"):
        for code in groups[g]:
            stock = parse_stock(code)
            if stock is None:
                missing.append((code, stock["errors"] if stock else ["报告不足 2 份"]))
    if missing:
        print(f"共 {len(missing)} 只解析失败/缺报告：")
        for code, errs in missing:
            print(f"  ❌ {code}: {'; '.join(errs)}")
    else:
        print("✅ 全部股票四视角报告均可解析")
    return missing


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="报告量化结论回填工具")
    parser.add_argument("--code", default=None, help="单只股票代码")
    parser.add_argument("--all", action="store_true", help="全量回填")
    parser.add_argument("--list-missing", action="store_true", help="列出解析失败标的")
    parser.add_argument("--show", action="store_true", help="仅打印聚合结果不写")
    parser.add_argument("--dry-run", action="store_true", help="不写文件")
    args = parser.parse_args()

    if args.list_missing:
        list_missing()
        return
    if args.code:
        r = sync_stock(args.code, dry_run=(args.dry_run or args.show))
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return
    if args.all:
        sync_all(dry_run=(args.dry_run or args.show))
        return
    parser.print_help()


if __name__ == "__main__":
    main()
