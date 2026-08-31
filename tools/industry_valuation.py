#!/usr/bin/env python3
"""industry_valuation.py — 行业估值难度分层工具（AI Berkshire · v8 估值方法层）。

核心思想：不同行业在当前宏观环境（利率下行/消费回落/地缘冲突/存量博弈）下的
估值难度差异巨大——保险 PE=5.5 是利差损陷阱，住宅开发 PE=56 是亏损样本假象。
常规 PE/PB/DCF 只对 ★~★★ 行业有效；★★★★+ 必须切换估值方法并穿透隐藏资产负债表。

知识库：data/industry_valuation_map.json（申万一级/二级行业难度评级 + 额外估值维度 + PE陷阱 + 首选方法）

子命令：
    lookup --code 601336            # 按股票代码查（读 stock_map 映射）
    lookup --industry 保险Ⅱ         # 按行业名查（模糊匹配，如"保险"也命中）
    map --code 601336 --industry 保险Ⅱ --level 2   # Agent 自维护映射（研究确认行业后回写）
    check-report --code 601336      # 报告估值方法合规检查（★★★★+ 行业的关键词闸门）
    list [--min-difficulty 4]       # 全景排名表
    macro                           # 当前宏观四特征快照

lookup 输出 JSON（供 Agent 消费）+ 人类可读卡片；check-report 返回
{"ok": bool, "industry": ..., "required_keywords": [...], "hit": [...], "missing": [...]}。
"""

import argparse
import json
import os
import sys
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_FILE = os.path.join(REPO_ROOT, "data", "industry_valuation_map.json")
REPORTS_DIR = os.path.join(REPO_ROOT, "reports")

# ★★★★+ 行业报告（财务估值视角）必须命中的关键词数（缺失 → 研究不合规，触发补写）
KEYWORD_MIN_HIT = 2


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_map():
    with open(MAP_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_map(data):
    with open(MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def stars(n: int) -> str:
    return "★" * int(n)


def resolve_industry(name: str):
    """行业名模糊解析：精确 → 一级表 → 二级表子串 → 父级反查。返回 (卡片, 命中名) 或 (None, None)。"""
    data = load_map()
    l1, l2 = data["level1"], data["level2"]
    if name in l2:
        return l2[name], name
    if name in l1:
        return l1[name], name
    # 子串模糊：查"保险"命中"保险Ⅱ"，查"股份制银行"先试子串
    for k in l2:
        if name in k or k.replace("Ⅱ", "") in name:
            return l2[k], k
    for k in l1:
        if name in k or k in name:
            return l1[k], k
    # 二级反查父级
    for k, v in l2.items():
        if v.get("parent") and (name in v["parent"] or v["parent"] in name):
            return v, k
    return None, None


def _card(industry: str, card: dict, level: int, source: str) -> dict:
    """构造标准化输出卡片。"""
    diff = int(card.get("difficulty", 3))
    out = {
        "industry": industry,
        "level": level,
        "source": source,
        "difficulty": diff,
        "difficulty_stars": stars(diff),
        "pe": card.get("pe"),
        "pb": card.get("pb"),
        "macro_hits": card.get("macro_hits", []),
        "extra_dimensions": card.get("extra_dimensions", []),
        "primary_methods": card.get("primary_methods", []),
        "pe_trap": card.get("pe_trap"),
        "hidden_sheets": card.get("hidden_sheets", []),
        "report_keywords": card.get("report_keywords", []),
        "discipline": load_map()["methodology"]["difficulty_discipline"].get(str(diff), ""),
    }
    if level == 1 and card.get("children"):
        out["children"] = card["children"]
        out["note"] = "一级行业评级为混合值，个股研究请进一步定位二级行业（children）"
    if level == 2 and card.get("parent"):
        # 附带一级行业难度，供交叉参考
        p = load_map()["level1"].get(card["parent"], {})
        out["parent"] = card["parent"]
        out["parent_difficulty"] = p.get("difficulty")
    return out


def _default_card(industry: str) -> dict:
    """知识库未收录行业的兜底卡片：按 ★★★ 处理（常规方法 + 需行业特定维度）。"""
    data = load_map()
    return {
        "industry": industry,
        "level": 0,
        "source": "未收录（fallback）",
        "difficulty": 3,
        "difficulty_stars": "★★★",
        "pe": None,
        "pb": None,
        "macro_hits": [],
        "extra_dimensions": [
            f"「{industry}」未收录在行业估值难度知识库中，按 ★★★ 处理",
            "自行判断该行业最接近知识库中的哪个一级行业并参考其评级",
            "判断该行业是否命中宏观四特征（利率下行/消费回落/地缘冲突/存量博弈），命中则 PE 失真风险上升",
            "确认后建议执行 map 子命令回写映射，供后续复用",
        ],
        "primary_methods": ["常规 PE/PB/DCF + 行业特定维度"],
        "pe_trap": None,
        "hidden_sheets": [],
        "report_keywords": [],
        "discipline": data["methodology"]["difficulty_discipline"]["3"],
    }


def lookup_by_code(code: str) -> dict:
    data = load_map()
    sm = data.get("stock_map", {})
    entry = sm.get(code)
    if not entry or code == "_comment":
        return {
            "ok": False, "code": code,
            "hint": f"代码 {code} 无行业映射。请自行判断其申万二级行业后执行: "
                    f"python tools/industry_valuation.py lookup --industry <行业名>，"
                    f"并用 map 子命令回写映射。",
        }
    industry = entry["industry"]
    level = int(entry.get("level", 2))
    card, hit = resolve_industry(industry)
    if card is None:
        out = _default_card(industry)
    else:
        out = _card(hit, card, level, source=f"stock_map→{industry}")
    out["ok"] = True
    out["code"] = code
    return out


def lookup_by_industry(name: str) -> dict:
    card, hit = resolve_industry(name)
    if card is None:
        out = _default_card(name)
        out["ok"] = True
        return out
    level = 2 if hit in load_map()["level2"] else 1
    out = _card(hit, card, level, source="industry_map")
    out["ok"] = True
    return out


def cmd_map(code: str, industry: str, level: int) -> dict:
    """Agent 回写股票→行业映射（研究确认行业后自维护）。"""
    if not code.isdigit() or len(code) != 6:
        return {"ok": False, "error": "code 须为 6 位数字股票代码"}
    if level not in (1, 2):
        return {"ok": False, "error": "level 须为 1 或 2"}
    data = load_map()
    card, hit = resolve_industry(industry)
    data.setdefault("stock_map", {})[code] = {
        "industry": hit if card else industry,
        "level": level,
        "mapped_at": datetime.now().strftime("%Y-%m-%d"),
        "mapped_by": "agent",
    }
    save_map(data)
    return {"ok": True, "code": code, "industry": hit if card else industry,
            "in_kb": card is not None}


def _find_valuation_report(code: str):
    """找该股的财务估值视角报告（时间最新）。"""
    best = None
    try:
        for fn in os.listdir(REPORTS_DIR):
            if not fn.endswith(".md") or not fn.startswith(code):
                continue
            if ("财务估值" not in fn) and ("估值" not in fn):
                continue
            path = os.path.join(REPORTS_DIR, fn)
            m = os.path.getmtime(path)
            if best is None or m > best[0]:
                best = (m, path)
    except OSError:
        pass
    return best[1] if best else None


def check_report(code: str, report_path: str = None) -> dict:
    """★★★★+ 行业的估值方法合规闸门：

    财务估值报告须命中 report_keywords 中至少 KEYWORD_MIN_HIT 个关键词，
    否则判定研究未按行业难度切换估值方法（如保险报告不提久期/EV = 还是拿PE在算）。
    ★★★ 及以下行业恒过（无强制关键词）。
    """
    lu = lookup_by_code(code)
    if not lu.get("ok") or lu.get("difficulty", 0) < 4:
        return {"ok": True, "checked": False, "reason": "无映射或难度<★★★★，免检",
                "lookup": lu}
    kws = lu.get("report_keywords", [])
    if not kws:
        return {"ok": True, "checked": False, "reason": "该行业无强制关键词", "lookup": lu}
    path = report_path or _find_valuation_report(code)
    if not path or not os.path.exists(path):
        return {"ok": False, "checked": True, "reason": "未找到财务估值报告",
                "lookup": lu, "required_keywords": kws, "hit": [], "missing": kws}
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return {"ok": False, "checked": True, "reason": f"报告读取失败: {path}",
                "lookup": lu, "required_keywords": kws, "hit": [], "missing": kws}
    hit = [k for k in kws if k in text]
    missing = [k for k in kws if k not in text]
    ok = len(hit) >= min(KEYWORD_MIN_HIT, len(kws))
    return {"ok": ok, "checked": True, "report": path,
            "industry": lu["industry"], "difficulty": lu["difficulty"],
            "required_keywords": kws, "hit": hit, "missing": missing,
            "required_min": min(KEYWORD_MIN_HIT, len(kws)),
            "reason": (f"命中 {len(hit)}/{len(kws)} 关键词"
                       + ("" if ok else f"，缺 {missing}——研究疑似未按★★★★+纪律切换估值方法"))}


def cmd_list(min_difficulty: int):
    data = load_map()
    rows = []
    for name, c in data["level2"].items():
        rows.append((int(c.get("difficulty", 3)), 2, name, c))
    for name, c in data["level1"].items():
        rows.append((int(c.get("difficulty", 3)), 1, name, c))
    rows.sort(key=lambda r: (-r[0], r[1], r[2]))
    print(f"{'难度':<7}{'层级':<5}{'行业':<14}{'中位PE':<8}{'中位PB':<8}首选方法")
    print("-" * 90)
    for diff, level, name, c in rows:
        if diff < min_difficulty:
            continue
        methods = "、".join(c.get("primary_methods", []))[:40]
        if not methods:
            methods = "拆分二级: " + ",".join(c.get("children", []))[:30] if c.get("children") else "-"
        pe = c.get("pe") if c.get("pe") is not None else "—"
        pb = c.get("pb") if c.get("pb") is not None else "—"
        print(f"{stars(diff):<7}{('二级' if level == 2 else '一级'):<5}{name:<14}{str(pe):<8}{str(pb):<8}{methods}")


def _print_card(c: dict):
    print("=" * 70)
    print(f"【{c['industry']}】估值难度 {c['difficulty_stars']}（difficulty={c['difficulty']}，{c['source']}）")
    if c.get("parent"):
        print(f"  所属一级行业: {c['parent']}（难度 {stars(c.get('parent_difficulty') or '?')}）")
    if c.get("pe") is not None or c.get("pb") is not None:
        print(f"  中位PE={c.get('pe') or '—'}  中位PB={c.get('pb') or '—'}")
    print(f"\n▶ 估值纪律: {c['discipline']}")
    if c.get("macro_hits"):
        print(f"\n▶ 命中宏观问题: {'；'.join(c['macro_hits'])}")
    if c.get("primary_methods"):
        print(f"\n▶ 首选估值方法: {'；'.join(c['primary_methods'])}")
    if c.get("extra_dimensions"):
        print("\n▶ 必须补充的估值维度:")
        for d in c["extra_dimensions"]:
            print(f"   {d}")
    if c.get("pe_trap"):
        print(f"\n▶ ⚠️ PE陷阱: {c['pe_trap']}")
    if c.get("hidden_sheets"):
        print("\n▶ 必须穿透的隐藏资产负债表:")
        for h in c["hidden_sheets"]:
            print(f"   - {h}")
    if c.get("report_keywords"):
        print(f"\n▶ 报告合规关键词（≥{KEYWORD_MIN_HIT}个）: {' / '.join(c['report_keywords'])}")
    if c.get("children"):
        print(f"\n▶ 二级细分: {', '.join(c['children'])}")
        if c.get("note"):
            print(f"   注: {c['note']}")
    print("=" * 70)


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="行业估值难度分层工具")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("lookup", help="查询行业估值难度卡片")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--code")
    g.add_argument("--industry")
    p.add_argument("--json", action="store_true", help="仅输出 JSON（Agent 模式）")

    p = sub.add_parser("map", help="回写股票→行业映射（Agent 自维护）")
    p.add_argument("--code", required=True)
    p.add_argument("--industry", required=True)
    p.add_argument("--level", type=int, choices=[1, 2], default=2)

    p = sub.add_parser("check-report", help="★★★★+ 行业报告估值方法合规检查")
    p.add_argument("--code", required=True)
    p.add_argument("--report", default=None)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("list", help="全景排名")
    p.add_argument("--min-difficulty", type=int, default=1)

    sub.add_parser("macro", help="宏观四特征快照")

    args = parser.parse_args()
    if args.cmd == "lookup":
        if args.code:
            out = lookup_by_code(args.code)
        else:
            out = lookup_by_industry(args.industry)
        if args.json or not out.get("ok"):
            print(json.dumps(out, ensure_ascii=False, indent=1))
        else:
            _print_card(out)
    elif args.cmd == "map":
        print(json.dumps(cmd_map(args.code, args.industry, args.level),
                         ensure_ascii=False, indent=1))
    elif args.cmd == "check-report":
        out = check_report(args.code, args.report)
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=1))
        else:
            status = "✅ 通过" if out.get("ok") else "❌ 未通过"
            print(f"{status} {args.code}"
                  + (f" [{out.get('industry')} {stars(out.get('difficulty') or 0)}]"
                     if out.get("industry") else ""))
            print(f"  {out.get('reason', '')}")
            if out.get("report"):
                print(f"  报告: {out['report']}")
    elif args.cmd == "list":
        cmd_list(args.min_difficulty)
    elif args.cmd == "macro":
        data = load_map()["macro_context"]
        print(f"宏观快照（{data['updated']}，{'已确认' if data.get('confirmed') else '待复核'}）:")
        for f in data["features"]:
            print(f"\n▶ {f['name']}\n   事实: {f['facts']}\n   影响: {f['impact']}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
