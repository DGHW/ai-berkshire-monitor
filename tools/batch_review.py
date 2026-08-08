#!/usr/bin/env python3
"""batch_review.py — 批量速评调度器（AI Berkshire 扩展 · Stage 2）。

为 /investment-team-lite 提供数据预填与任务管理：
  1. 从初筛池读取候选股票（默认 Layer1 通过的 890 只）
  2. 对每只预填数据卡（行情 + 财务指标 + 冷信息信号）→ 写入 task 文件
  3. Agent 只需读数据卡做判断（5-6 分钟/只），无需重新搜索基础数据
  4. 并发 5-6 只、断点续跑、progress 跟踪

用法：
    python3 tools/batch_review.py prepare --limit 30       # 为前30只生成数据卡
    python3 tools/batch_review.py prepare --pool small     # 只准备 <80亿 小盘
    python3 tools/batch_review.py prepare --batch 0        # 分批（每批30只，多窗口用）
    python3 tools/batch_review.py status                   # 查看进度
    python3 tools/batch_review.py claim                    # 领取下一只未处理的（Agent用）
    python3 tools/batch_review.py done 002272 --verdict PASS --summary "..."  # 提交结果
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREEN_FILE = os.path.join(REPO_ROOT, "data", "screening", "stage1_pool.json")
BATCH_DIR = os.path.join(REPO_ROOT, "data", "screening", "batch")
PROGRESS_FILE = os.path.join(BATCH_DIR, "progress.json")

BATCH_SIZE = 30  # 每批 30 只（对应一批 Agent 会话）

# 数据卡包含的字段
CARD_FIELDS = [
    "ts_code", "name", "industry", "pe_ttm", "total_mv",
    "roe_yearly", "roe", "gross_margin", "net_margin",
    "debt_ratio", "rev_yoy", "latest_n_income",
]


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"tasks": {}, "batch_size": BATCH_SIZE}


def save_progress(prog: dict):
    os.makedirs(BATCH_DIR, exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(prog, f, ensure_ascii=False, indent=2)


def load_screen() -> dict:
    with open(SCREEN_FILE, encoding="utf-8") as f:
        return json.load(f)


def fnum(v, nd=2):
    if v is None:
        return "-"
    try:
        if v != v:
            return "-"
        return f"{v:,.{nd}f}"
    except (ValueError, TypeError):
        return "-"


# ---------------------------------------------------------------------------
# prepare：为候选生成数据卡
# ---------------------------------------------------------------------------
def prepare(limit: int = None, pool_filter: str = "all", batch: int = None):
    screen = load_screen()
    stocks = screen["stocks"]
    prog = load_progress()

    # 候选池：Layer1 通过（或 PE 缺失待确认的）
    candidates = []
    for ts, s in stocks.items():
        if not s.get("layer1_pass"):
            continue
        mv = s.get("total_mv") or 0
        if pool_filter == "small" and mv / 1e8 >= 80:
            continue
        if pool_filter == "large" and mv / 1e8 < 80:
            continue
        candidates.append(ts)
    candidates.sort()

    # 分批
    if batch is not None:
        start = batch * BATCH_SIZE
        candidates = candidates[start:start + BATCH_SIZE]
        print(f"[prepare] 批次 {batch}：{len(candidates)} 只（{start}-{start+len(candidates)}）")
    elif limit:
        candidates = candidates[:limit]
        print(f"[prepare] 限量：{len(candidates)} 只")

    # 冷信息挖掘（可选，慢；默认跳过，Agent 需要时单独跑）
    created = 0
    for ts in candidates:
        if ts in prog["tasks"] and prog["tasks"][ts].get("card"):
            continue
        s = stocks[ts]
        card = {f: s.get(f) for f in CARD_FIELDS}
        card["ts_code"] = ts
        card["mv_yi"] = round((s.get("total_mv") or 0) / 1e8, 1)
        card["card_ts"] = datetime.now().isoformat(timespec="seconds")
        prog["tasks"].setdefault(ts, {})
        prog["tasks"][ts]["card"] = card
        prog["tasks"][ts]["status"] = "pending"
        created += 1

    save_progress(prog)
    print(f"[prepare] 新增数据卡 {created} 张，总任务 {len(prog['tasks'])}")
    # 统计
    done = sum(1 for t in prog["tasks"].values() if t.get("status") == "done")
    inprog = sum(1 for t in prog["tasks"].values() if t.get("status") == "in_progress")
    print(f"[prepare] 已完成 {done}，进行中 {inprog}，待处理 {len(prog['tasks']) - done - inprog}")


# ---------------------------------------------------------------------------
# status：进度
# ---------------------------------------------------------------------------
def status():
    prog = load_progress()
    tasks = prog.get("tasks", {})
    if not tasks:
        print("暂无任务，先运行 prepare")
        return
    done = sum(1 for t in tasks.values() if t.get("status") == "done")
    inprog = sum(1 for t in tasks.values() if t.get("status") == "in_progress")
    pending = len(tasks) - done - inprog
    passed = sum(1 for t in tasks.values() if t.get("verdict") == "PASS")
    print(f"总任务 {len(tasks)} | 待处理 {pending} | 进行中 {inprog} | 完成 {done} | 通过 {passed}")
    # 列出进行中的
    for ts, t in tasks.items():
        if t.get("status") == "in_progress":
            print(f"  ⏳ {ts} {t.get('card', {}).get('name', '')} (started {t.get('started_at', '?')})")


# ---------------------------------------------------------------------------
# claim：Agent 领取任务
# ---------------------------------------------------------------------------
def claim(worker: str = "default"):
    prog = load_progress()
    tasks = prog.get("tasks", {})
    # 找第一只 pending 的
    for ts, t in tasks.items():
        if t.get("status") == "pending":
            t["status"] = "in_progress"
            t["worker"] = worker
            t["started_at"] = datetime.now().isoformat(timespec="seconds")
            save_progress(prog)
            print(json.dumps({"ts_code": ts, "card": t["card"]}, ensure_ascii=False, indent=2))
            return
    print(json.dumps({"ts_code": None, "card": None}, ensure_ascii=False))
    print("没有待处理任务")


# ---------------------------------------------------------------------------
# done：提交结果
# ---------------------------------------------------------------------------
def done(ts: str, verdict: str, summary: str, score: float = None):
    prog = load_progress()
    tasks = prog.get("tasks", {})
    if ts not in tasks:
        print(f"❌ {ts} 不在任务列表")
        return
    t = tasks[ts]
    t["status"] = "done"
    t["verdict"] = verdict.upper()
    t["summary"] = summary
    if score is not None:
        t["score"] = score
    t["done_at"] = datetime.now().isoformat(timespec="seconds")
    save_progress(prog)
    print(f"✅ {ts} 完成：{verdict}")


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="批量速评调度器")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("prepare", help="生成数据卡")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--pool", choices=["all", "small", "large"], default="all")
    p.add_argument("--batch", type=int, default=None)

    sub.add_parser("status", help="查看进度")

    p = sub.add_parser("claim", help="领取任务（Agent用）")
    p.add_argument("--worker", default="default")

    p = sub.add_parser("done", help="提交结果")
    p.add_argument("ts_code")
    p.add_argument("--verdict", required=True, choices=["PASS", "HOLD", "FAIL"])
    p.add_argument("--summary", required=True)
    p.add_argument("--score", type=float, default=None)

    args = parser.parse_args()

    if args.cmd == "prepare":
        prepare(limit=args.limit, pool_filter=args.pool, batch=args.batch)
    elif args.cmd == "status":
        status()
    elif args.cmd == "claim":
        claim(worker=args.worker)
    elif args.cmd == "done":
        done(args.ts_code, args.verdict, args.summary, args.score)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
