#!/usr/bin/env python3
"""agent_driver.py — 定时任务 Agent 驱动（AI Berkshire 买卖闭环 v6）。

在 Windows 计划任务（schtasks → batch1/batch2）中无人值守地调用 CodeBuddy
CLI（headless `-p` 模式）驱动投资研究技能，并把研究结果自动回填与买入。

子命令：
    review --limit N [--dry-run]           # batch1：REVIEW_DUE → 完整重研 → 闸门 → 自动买入
    batch2-research [--lite-cap N] [--dry-run]  # batch2：边界 full + 其余 lite → report_sync
    run-one --code X --mode full|lite [--overwrite]  # 单只调试/人工触发
    status                               # 队列 + 锁状态
    retry-failed                         # 重试 failed 任务

关键约束（LOGIC.md v6 宪法）：
  - 成功判定以落盘报告文件存在 + ## 量化结论 可解析为唯一权威（stdout json 不可信）
  - 自动买入六道闸门全过才执行 --add（详见 execute_buy docstring）
  - 并发：full=1（内部已 4 Agent），lite=2
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import pool_kelly
from report_sync import parse_report, find_reports, sync_stock, parse_stock

CODEBUDDY = os.environ.get(
    "CODEBUDDY_BIN", r"C:\Users\17356\AppData\Roaming\npm\codebuddy.cmd"
)

QUEUE_FILE = os.path.join(REPO_ROOT, "data", "monitor", "agent_queue.json")
LOCK_FILE = os.path.join(REPO_ROOT, "data", "monitor", "agent.lock")
GROUPS_FILE = os.path.join(REPO_ROOT, "data", "monitor", "portfolio_groups.json")
POOL_FILE = os.path.join(REPO_ROOT, "data", "monitor", "pool.json")
ROTATION_STATE = os.path.join(REPO_ROOT, "data", "monitor", "rotation_state.json")
CASH_FILE = os.path.join(REPO_ROOT, "data", "positions", "portfolio_cash.json")
FUTU_CONFIG_FILE = os.path.join(REPO_ROOT, "data", "positions", "futu_config.json")
AGENT_LOG_DIR = os.path.join(REPO_ROOT, "reports", "monitor", "agent_runs")

LOCK_STALE_SECONDS = 24 * 3600  # 锁 24h 陈旧可抢占
MAX_TRIES = 2
FULL_TIMEOUT_MIN = 90
LITE_TIMEOUT_MIN = 20
FULL_MAX_TURNS = 120
LITE_MAX_TURNS = 40
FULL_CONCURRENCY = 1
LITE_CONCURRENCY = 2

# 完整重研要写的四视角文件（提示词内用绝对路径）
FULL_FILES = [
    "商业模式-段永平视角",
    "财务估值-巴菲特视角",
    "行业竞争-芒格视角",
    "风险评估-李录视角",
]


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8_stdio()  # 模块级立即生效（含被 import 时）


def load_json(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)


# ---------------------------------------------------------------------------
# 锁
# ---------------------------------------------------------------------------
def acquire_lock(timeout_s: int = 30) -> bool:
    if os.path.exists(LOCK_FILE):
        age = time.time() - os.path.getmtime(LOCK_FILE)
        if age < LOCK_STALE_SECONDS:
            log(f"🔒 另一任务在跑（锁存在 {age:.0f}s），退出")
            return False
        log(f"⚠️ 锁已陈旧（{age:.0f}s），抢占")
        os.remove(LOCK_FILE)
    try:
        with open(LOCK_FILE, "x", encoding="utf-8") as f:
            f.write(json.dumps({"pid": os.getpid(), "ts": datetime.now().isoformat()}))
        return True
    except FileExistsError:
        return False


def release_lock():
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 队列
# ---------------------------------------------------------------------------
def load_queue():
    return load_json(QUEUE_FILE)


def save_queue(q):
    save_json(QUEUE_FILE, q)


def enqueue(code: str, name: str, mode: str, reason: str, source: str) -> bool:
    q = load_queue()
    today = datetime.now().strftime("%Y-%m-%d")
    for job in q.get("jobs", []):
        if job["code"] == code and job["status"] in ("pending", "in_progress"):
            return False
        if job["code"] == code and job["status"] == "done" and job.get("finished", "").startswith(today):
            return False
    q.setdefault("jobs", []).append({
        "code": code, "name": name, "mode": mode, "reason": reason,
        "source": source, "status": "pending", "tries": 0, "max_tries": MAX_TRIES,
        "created": today, "started": None, "finished": None,
        "verdict": None, "note": "",
    })
    q["updated"] = today
    save_queue(q)
    log(f"📥 入队 {code} {name} [{mode}]：{reason}")
    return True


def claim_next(mode: str):
    q = load_queue()
    for job in q.get("jobs", []):
        if job["status"] == "pending" and job["mode"] == mode:
            job["status"] = "in_progress"
            job["started"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_queue(q)
            return job
    return None


def mark(code, status, verdict=None, note="", run_id=None):
    q = load_queue()
    for job in q.get("jobs", []):
        if job["code"] == code:
            job["status"] = status
            job["finished"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if verdict is not None:
                job["verdict"] = verdict
            if note:
                job["note"] = note
            if run_id:
                job["run_id"] = run_id
            break
    q["updated"] = datetime.now().strftime("%Y-%m-%d")
    save_queue(q)


# ---------------------------------------------------------------------------
# codebuddy 子进程
# ---------------------------------------------------------------------------
def run_codebuddy(prompt: str, *, timeout_min: int, max_turns: int, run_id: str) -> dict:
    os.makedirs(AGENT_LOG_DIR, exist_ok=True)
    log_path = os.path.join(AGENT_LOG_DIR, f"{run_id}.log")
    cmd = [CODEBUDDY, "-p", prompt, "-y",
           "--permission-mode", "bypassPermissions",
           "--output-format", "json",
           "--max-turns", str(max_turns)]
    env = {**os.environ,
           "CODEBUDDY_RETRY_WATCHDOG": "1",
           "CODEBUDDY_CODE_MAX_TURNS": str(max_turns),
           "CODEBUDDY_CODE_DISABLE_BACKGROUND_TASKS": "1",
           "PYTHONIOENCODING": "utf-8"}
    log(f"🚀 codebuddy 启动: {run_id}（timeout={timeout_min}min, max_turns={max_turns}）")
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                              timeout=timeout_min * 60, env=env)
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        err = (proc.stderr or b"").decode("utf-8", errors="replace")
        timed_out = False
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", errors="replace")
        err = f"TIMEOUT after {timeout_min}min\n{(e.stderr or b'').decode('utf-8', errors='replace')}"
        timed_out = True
        proc = None
    duration = int(time.time() - t0)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"# run {run_id} | exit={proc.returncode if proc else 'TIMEOUT'} | {duration}s\n\n"
                f"## PROMPT\n{prompt}\n\n## STDOUT\n{out}\n\n## STDERR\n{err}\n")
    log(f"🏁 codebuddy 结束: {run_id}（exit={proc.returncode if proc else 'TIMEOUT'}，{duration}s）")
    return {"exit_code": proc.returncode if proc else -1, "stdout": out,
            "stderr": err, "timed_out": timed_out, "run_id": run_id, "log_path": log_path}


def _build_prompt_full(code: str, name: str, overwrite: bool) -> str:
    files = "\n".join(
        f"- reports/{code}{name}-{suffix}.md"
        for suffix in FULL_FILES)
    return f"""/investment-team {code} {name}

【输出规范】研究完成后，在 ai-berkshire 工作区的 reports/ 目录下写入以下四个文件（{'覆盖既有报告' if overwrite else '若已存在则覆盖'}）：
{files}
每份文件必须以 "## 量化结论" 小节结尾，严格包含五行（字段名与格式供机器解析，禁止改动）：
- 内在涨幅: **X%**
- 击球区: 🟢/🟡/🔴 结论一句话
- 目标建仓价: **X 元**
- 二次补仓价: **X 元**
- 数据核验: ✓/⚠️ 说明

【强制数据核验】财务数据必须调用 python tools/financial_rigor.py cross-validate 交叉验证，两源不一致须在数据核验字段标注。

【反锚定效应（硬约束）】建仓价必须基于独立估值推导（三情景估值/股息安全垫/合理PB等），严禁锚定当前股价。若你的建仓价与现价差距<10%，必须自问：这是估值结论还是锚定效应？建仓价应与现价无关——现价翻倍或腰斩，你的建仓价都应不变。

【headless 说明】自动模式跳过交互确认直接执行；不要保存到用户主目录。完成后打印：RESEARCH_DONE {code}
"""


def _build_prompt_lite(code: str, name: str) -> str:
    return f"""/investment-team-lite {code} {name}

【headless 自动模式】
1. 跳过批量调度器的 claim 步骤（股票已由调用方指定），直接对该标的做六关速评（能力圈/好生意/护城河/管理层/估值/财务健康）。
2. 优先参考 reports/ 下该股票的既有报告判断基本面是否证伪；可用 tools/quote_fetcher.py {code} 取实时价。
3. 最后严格输出如下 JSON（不要输出其它内容）：
   {{"verdict": "PASS|HOLD|FAIL", "reason": "一句话理由", "gain_med_updated": null}}
   其中 gain_med_updated 仅在你能给出比既有报告更新的内在涨幅估算时填数字（%），否则填 null。
4. 若 WebSearch 不可用，禁止用训练知识冒充联网结果，在 reason 中标注"未联网"。
"""


LITE_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "HOLD", "FAIL"]},
        "reason": {"type": "string"},
        "gain_med_updated": {"type": ["number", "null"]},
    },
    "required": ["verdict", "reason"],
})


# ---------------------------------------------------------------------------
# 研究成功判定（唯一权威：落盘文件 + 可解析）
# ---------------------------------------------------------------------------
def research_succeeded(code: str):
    """检查该代码四视角文件：≥3 份可解析算成功。返回 (ok, detail)。"""
    reports, synth = find_reports(code)
    ok_count = 0
    detail = []
    for view in ("商业模式", "财务估值", "行业竞争", "风险评估"):
        path = reports.get(view)
        if not path:
            detail.append(f"缺{view}")
            continue
        r = parse_report(path)
        if r is None:
            detail.append(f"{view}解析失败")
        else:
            ok_count += 1
    ok = ok_count >= 3
    detail.append(f"可解析 {ok_count}/4")
    return ok, "; ".join(detail)


def _build_prompt_normalize(code: str, name: str) -> str:
    """把综合研究报告拆写为四个规范视角文件（简单任务，Agent 遵守度高）。"""
    files = "\n".join(
        f"- reports/{code}{name}-{suffix}.md"
        for suffix in FULL_FILES)
    return f"""在当前 ai-berkshire 工作区的 reports/ 目录下，找到文件名含"{name}"且修改时间最新的一份研究报告（可能是综合投资研究报告或单视角报告），通读后拆写为以下四个独立文件（markdown 列表即文件名）：

{files}

要求：
1. 每份文件是完整独立的视角研究报告，内容来自该报告中对应维度的分析（信息不足的维度基于报告数据合理补全，不得虚构数据）
2. 每份文件必须以 "## 量化结论" 小节结尾，严格包含五行（供机器解析，字段名与格式禁止改动）：
- 内在涨幅: **X%**
- 击球区: 🟢/🟡/🔴 结论一句话
- 目标建仓价: **X 元**
- 二次补仓价: **X 元**
- 数据核验: ✓/⚠️ 说明
3. 若报告中无明确建仓价，基于其中估值区间取中值估算，并在数据核验字段标注"估算"
4. 四个文件全部写入后，用 Bash 执行 ls 确认存在，最后打印：RESEARCH_DONE {code}
"""


def normalize_reports(code: str, name: str, run_id: str) -> bool:
    """二次 codebuddy 调用：把综合报告拆写为四视角规范文件。返回是否成功。"""
    prompt = _build_prompt_normalize(code, name)
    run = run_codebuddy(prompt, timeout_min=30, max_turns=60,
                        run_id=f"{run_id}_norm")
    ok, detail = research_succeeded(code)
    log(f"📝 落盘归一: {detail}")
    return ok


def _extract_lite_verdict(run) -> dict:
    """从 lite 运行 stdout 提取 verdict JSON。"""
    m = re.search(r"\{[^{}]*\"verdict\"[^{}]*\}", run["stdout"])
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# 自动买入（六道闸门）
# ---------------------------------------------------------------------------
def execute_buy(code: str, old_gain: float, dry_run: bool) -> dict:
    """全自动买入：六道闸门全过 → position_manager --add。
    闸门：① 四视角可解析 ② gain_med>0 ③ zone_all_red==False
          ④ 新涨幅≥旧涨幅-10 ⑤ 实时现价≤buy_zone.high ⑥ kelly f>0"""
    groups = load_json(GROUPS_FILE)
    pool = load_json(POOL_FILE)
    stock = parse_stock(code)
    if stock is None:
        return {"bought": False, "reason": "四视角报告不足 2 份可解析"}

    gain = stock["gain_med"]
    entry_med = stock["entry_med"]
    if gain <= 0:
        return {"bought": False, "reason": f"新内在涨幅 {gain}% ≤ 0"}
    if stock["zone_all_red"]:
        return {"bought": False, "reason": "击球区 ≥3🔴"}
    if old_gain is not None and gain < old_gain - 10:
        return {"bought": False, "reason": f"涨幅恶化：{old_gain}% → {gain}%（下滑超10pct）"}

    # 实时现价
    try:
        sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
        from quote_fetcher import get_spot
        spot = get_spot(code)
        price = float(spot["price"])
    except Exception as e:
        price = pool.get("stocks", {}).get(code, {}).get("last_price")
        log(f"⚠️ 实时价获取失败({e})，用 pool 缓存价 {price}")

    if not price:
        return {"bought": False, "reason": "无法获取现价"}
    zone_hi = (pool.get("stocks", {}).get(code, {}).get("buy_zone") or {}).get("high")
    if zone_hi and price > zone_hi:
        return {"bought": False, "reason": f"现价 {price} 超出击球区上沿 {zone_hi}"}

    # 凯利
    basis = pool_kelly.load_basis()
    if not basis:
        basis = pool_kelly.pool_stats(groups)
    r = pool_kelly.kelly_for(code, groups, basis)
    if "error" in r:
        return {"bought": False, "reason": f"凯利错误: {r['error']}"}
    if r["suggested_pct"] <= 0:
        return {"bought": False, "reason": f"凯利仓位 0%（{r['note']}）"}

    # 股数
    cash = load_json(CASH_FILE)
    total_cash = cash.get("total_cash", 0)
    pct = r["suggested_pct"]
    amount = total_cash * pct / 100
    shares = int(amount / price / 100) * 100
    if shares < 100:
        return {"bought": False, "reason": f"股数 {shares} < 100（凯利 {pct}% × 池 {total_cash} = {amount:.0f} 元 @ {price}）"}
    if shares * price > total_cash:
        return {"bought": False, "reason": f"金额 {shares*price:.0f} 超资金池 {total_cash}"}

    log(f"💰 六闸门全过：{code} 凯利{pct}% → {shares}股 @ {price}（金额 {shares*price:.0f}）")
    if dry_run:
        return {"bought": False, "dry_run": True, "reason": "dry-run",
                "shares": shares, "price": price, "kelly_pct": pct}
    # 执行买入（记账）
    subprocess.run([sys.executable, os.path.join(REPO_ROOT, "tools", "position_manager.py"),
                    "--add", code, str(shares), str(price),
                    "--reason", f"auto-review-pass:gain{gain}%"], cwd=REPO_ROOT)
    # pool.status → BOUGHT（让 --rotate 识别空位）
    if code in pool.get("stocks", {}):
        pool["stocks"][code]["status"] = "BOUGHT"
        pool["stocks"][code]["note"] = f"自动买入 {shares}股@{price}"
        pool["updated"] = datetime.now().strftime("%Y-%m-%d")
        save_json(POOL_FILE, pool)
    # rotation_state 记录
    state = load_json(ROTATION_STATE)
    state.setdefault("reviews", []).append({
        "code": code, "date": datetime.now().strftime("%Y-%m-%d"),
        "status": "DONE", "verdict": "PASS", "gain_med": gain,
        "shares": shares, "price": price, "source": "agent_driver"})
    save_json(ROTATION_STATE, state)

    # 双轨：富途模拟盘下单（失败仅告警，绝不影响记账与状态机）
    futu_res = {"ok": False, "reason": "futu 未启用"}
    try:
        futu_cfg = load_json(FUTU_CONFIG_FILE)
        if futu_cfg.get("enabled", True):
            from futu_bridge import cmd_buy as futu_cmd_buy  # 复用同模块逻辑
            # 直接调用封装函数（dry_run 由 futu_config 控制）
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                futu_cmd_buy(code, shares, price, dry_run=False)
            out = buf.getvalue().strip()
            try:
                futu_res = json.loads(out.splitlines()[-1])
            except Exception:
                futu_res = {"ok": False, "reason": out[-200:]}
            log(f"📡 富途模拟盘: {out[:300]}")
    except Exception as e:
        futu_res = {"ok": False, "reason": f"futu_bridge 调用异常: {e}"}
        log(f"⚠️ 富途模拟盘异常（不影响记账）: {e}")
    # 把模拟盘结果附加到 pool note
    if code in pool.get("stocks", {}):
        futu_note = "futu✅" if futu_res.get("ok") else f"futu⏸({futu_res.get('reason','?')})"
        pool["stocks"][code]["note"] = f"{pool['stocks'][code].get('note','')}; {futu_note}"
        save_json(POOL_FILE, pool)

    return {"bought": True, "shares": shares, "price": price, "kelly_pct": pct,
            "gain_med": gain, "futu": futu_res}


# ---------------------------------------------------------------------------
# 主流程 1：每日 review（REVIEW_DUE → 完整重研 → 闸门 → 自动买入）
# ---------------------------------------------------------------------------
def review_once(limit: int, dry_run: bool) -> dict:
    if not acquire_lock():
        return {"error": "locked"}
    try:
        pool = load_json(POOL_FILE)
        due = [c for c, s in pool.get("stocks", {}).items()
               if s.get("status") == "REVIEW_DUE"]
        due.sort()
        if not due:
            log("✅ 无 REVIEW_DUE 标的")
            return {"reviewed": []}
        log(f"📋 REVIEW_DUE 标的: {due}（本批上限 {limit}）")
        reviewed = []
        for code in due[:limit]:
            name = pool["stocks"][code].get("name_cn", "")
            groups = load_json(GROUPS_FILE)
            old_gain = None
            for g in ("buy", "watch", "reserve", "drop"):
                if code in groups.get(g, {}):
                    old_gain = groups[g][code].get("gain_med")
                    break
            enqueue(code, name, "full", "REVIEW_DUE", "batch1")
            job = claim_next("full")
            if not job:
                continue
            if dry_run:
                log(f"🔍 [dry-run] {code} {name} 将执行完整重研+自动买入评估")
                run = {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False,
                       "run_id": f"dryrun_{code}", "log_path": ""}
            else:
                run_id = f"review_{code}_{datetime.now().strftime('%H%M%S')}"
                run = run_codebuddy(_build_prompt_full(code, name, overwrite=True),
                                    timeout_min=FULL_TIMEOUT_MIN, max_turns=FULL_MAX_TURNS,
                                    run_id=run_id)
            ok, detail = research_succeeded(code)
            if not ok and not dry_run:
                # 研究完成但四文件未落盘 → 归一
                log(f"⚠️ 四文件未齐全（{detail}），执行落盘归一")
                normalize_reports(code, name, run["run_id"])
                ok, detail = research_succeeded(code)
            if not ok and not dry_run:
                mark(code, "failed", note=f"研究未落盘: {detail}; exit={run['exit_code']}", run_id=run["run_id"])
                log(f"❌ {code} 研究失败: {detail}")
                continue
            if dry_run:
                mark(code, "done", verdict="DRY_RUN", note="dry-run 未真跑", run_id="dryrun")
            else:
                mark(code, "done", verdict="RESEARCH_OK", note=detail, run_id=run["run_id"])
            # 回填（dry-run 不写文件）
            sync_stock(code, dry_run=dry_run)
            # 六道闸门自动买入
            res = execute_buy(code, old_gain, dry_run=dry_run)
            reviewed.append({"code": code, "research": detail, "buy": res})
            if res.get("bought"):
                log(f"✅ {code} 自动买入 {res['shares']}股@{res['price']}")
            else:
                log(f"⏸ {code} 未买入: {res.get('reason')}")
            if not dry_run and len(reviewed) >= limit:
                break
        return {"reviewed": reviewed}
    finally:
        release_lock()


# ---------------------------------------------------------------------------
# 主流程 2：batch2 混合深度研究（边界 full + 其余 lite → report_sync）
# ---------------------------------------------------------------------------
def batch2_research(lite_cap: int, dry_run: bool) -> dict:
    if not acquire_lock():
        return {"error": "locked"}
    try:
        # 1. 边界清单
        import pool_rotator
        plan = pool_rotator.rotation_plan()
        boundary = {b["code"]: b for b in plan["boundary"]}
        log(f"📋 轮动边界标的: {len(boundary)} 只 → 完整版重研")

        # 2. 边界标的 full 重研（并发 1）
        full_done = []
        for code, info in boundary.items():
            name = info["name"]
            enqueue(code, name, "full", f"boundary:{info['action']}", "batch2")
            job = claim_next("full")
            if not job:
                continue
            if dry_run:
                log(f"🔍 [dry-run] {code} {name} full 重研跳过")
                mark(code, "done", verdict="DRY_RUN")
                continue
            run_id = f"b2full_{code}_{datetime.now().strftime('%H%M%S')}"
            run = run_codebuddy(_build_prompt_full(code, name, overwrite=True),
                                timeout_min=FULL_TIMEOUT_MIN, max_turns=FULL_MAX_TURNS,
                                run_id=run_id)
            ok, detail = research_succeeded(code)
            if not ok:
                # 归一：拆写综合报告为四视角规范文件
                log(f"⚠️ 四文件未齐全（{detail}），执行落盘归一")
                normalize_reports(code, name, run_id)
                ok, detail = research_succeeded(code)
            if not ok:
                mark(code, "failed", note=f"研究未落盘: {detail}", run_id=run_id)
                full_done.append({"code": code, "ok": False, "detail": detail})
                continue
            mark(code, "done", verdict="RESEARCH_OK", note=detail, run_id=run_id)
            sync_stock(code, dry_run=False)
            full_done.append({"code": code, "ok": True, "detail": detail})
            log(f"✅ 边界标的 {code} {name} 重研完成")

        # 3. 其余 lite 速评（并发 2，cap 上限）
        groups = load_json(GROUPS_FILE)
        lite_candidates = []
        for g in ("watch", "reserve", "drop"):
            for c, s in groups.get(g, {}).items():
                if c in boundary or c in full_done and full_done[-1].get("code") == c:
                    continue
                if any(j["code"] == c and j["status"] in ("pending", "in_progress", "done")
                       and j.get("finished", "").startswith(datetime.now().strftime("%Y-%m-%d"))
                       for j in load_queue().get("jobs", [])):
                    continue
                lite_candidates.append((c, s.get("name", "")))
        lite_candidates = lite_candidates[:lite_cap]
        log(f"🔍 lite 速评候选: {len(lite_candidates)} 只（cap={lite_cap}）")

        lite_done = []
        if not dry_run and lite_candidates:
            def _lite_one(item):
                code, name = item
                if not enqueue(code, name, "lite", "batch2-rotate", "batch2"):
                    return None
                job = claim_next("lite")
                if not job:
                    return None
                run_id = f"b2lite_{code}_{datetime.now().strftime('%H%M%S')}"
                run = run_codebuddy(_build_prompt_lite(code, name),
                                    timeout_min=LITE_TIMEOUT_MIN, max_turns=LITE_MAX_TURNS,
                                    run_id=run_id)
                verdict = _extract_lite_verdict(run)
                if verdict is None:
                    mark(code, "failed", note=f"无 verdict JSON; exit={run['exit_code']}", run_id=run_id)
                    return {"code": code, "ok": False, "detail": "无verdict"}
                mark(code, "done", verdict=verdict["verdict"],
                     note=verdict.get("reason", ""), run_id=run_id)
                return {"code": code, "ok": True, "verdict": verdict["verdict"],
                        "reason": verdict.get("reason", "")}

            with ThreadPoolExecutor(max_workers=LITE_CONCURRENCY) as ex:
                for fut in as_completed([ex.submit(_lite_one, item) for item in lite_candidates]):
                    r = fut.result()
                    if r:
                        lite_done.append(r)
                        log(f"🔍 {r['code']} lite → {r.get('verdict', '?')}")

        # 4. 回填 + 结果汇总
        if not dry_run:
            from report_sync import sync_all
            sync_all(dry_run=False)
        return {"boundary": len(boundary), "full_done": full_done,
                "lite_done": lite_done}
    finally:
        release_lock()


# ---------------------------------------------------------------------------
# run-one / status / retry-failed
# ---------------------------------------------------------------------------
def run_one(code: str, mode: str, overwrite: bool, dry_run: bool):
    groups = load_json(GROUPS_FILE)
    name = None
    for g in ("buy", "watch", "reserve", "drop"):
        if code in groups.get(g, {}):
            name = groups[g][code].get("name")
            break
    if not name:
        pool = load_json(POOL_FILE)
        name = pool.get("stocks", {}).get(code, {}).get("name_cn", "")
    if not name:
        log(f"❌ {code} 未找到名称")
        return
    enqueue(code, name, mode, "manual", "run-one")
    job = claim_next(mode)
    if not job:
        log("❌ 入队失败")
        return
    if mode == "full":
        run = run_codebuddy(_build_prompt_full(code, name, overwrite=overwrite),
                            timeout_min=FULL_TIMEOUT_MIN, max_turns=FULL_MAX_TURNS,
                            run_id=f"one_{code}_{datetime.now().strftime('%H%M%S')}")
        ok, detail = research_succeeded(code)
        if not ok:
            # 研究完成但四文件未落盘 → 归一：拆写综合报告为四视角规范文件
            log(f"⚠️ 四文件未齐全（{detail}），执行落盘归一")
            norm_ok = normalize_reports(code, name, run["run_id"])
            ok, detail = research_succeeded(code)
        if ok:
            mark(code, "done", verdict="RESEARCH_OK", note=detail, run_id=run["run_id"])
            sync_stock(code, dry_run=False)
            log(f"✅ {code} 重研成功并回填: {detail}")
        else:
            mark(code, "failed", note=detail, run_id=run["run_id"])
            log(f"❌ {code} 重研失败: {detail}")
        return run
    else:
        run = run_codebuddy(_build_prompt_lite(code, name),
                            timeout_min=LITE_TIMEOUT_MIN, max_turns=LITE_MAX_TURNS,
                            run_id=f"one_{code}_{datetime.now().strftime('%H%M%S')}")
        v = _extract_lite_verdict(run)
        if v:
            mark(code, "done", verdict=v["verdict"], note=v.get("reason", ""), run_id=run["run_id"])
            log(f"✅ {code} lite → {v['verdict']}: {v.get('reason')}")
        else:
            mark(code, "failed", note="无 verdict JSON", run_id=run["run_id"])
            log(f"❌ {code} lite 无 verdict")
        return run


def status():
    q = load_queue()
    jobs = q.get("jobs", [])
    print(f"队列任务: {len(jobs)}")
    for j in jobs[-20:]:
        print(f"  [{j['status']}] {j['code']} {j.get('name','')} "
              f"({j['mode']}) tries={j.get('tries',0)}/{(j.get('max_tries',MAX_TRIES))} "
              f"{j.get('reason','')} {j.get('note','')}")
    if os.path.exists(LOCK_FILE):
        print(f"🔒 锁存在: {open(LOCK_FILE, encoding='utf-8').read()}")
    else:
        print("🔓 无锁")


def retry_failed():
    q = load_queue()
    changed = 0
    for job in q.get("jobs", []):
        if job["status"] == "failed" and job.get("tries", 0) < job.get("max_tries", MAX_TRIES):
            job["status"] = "pending"
            job["tries"] = job.get("tries", 0) + 1
            changed += 1
    if changed:
        save_queue(q)
    print(f"重试 {changed} 个 failed 任务")


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="定时任务 Agent 驱动")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("review", help="每日 REVIEW_DUE 自动复核")
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("batch2-research", help="轮动混合深度研究")
    p.add_argument("--lite-cap", type=int, default=30)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("run-one", help="单只调试")
    p.add_argument("--code", required=True)
    p.add_argument("--mode", choices=["full", "lite"], required=True)
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    sub.add_parser("status", help="队列+锁状态")
    sub.add_parser("retry-failed", help="重试失败任务")

    args = parser.parse_args()
    if args.cmd == "review":
        review_once(args.limit, args.dry_run)
    elif args.cmd == "batch2-research":
        batch2_research(args.lite_cap, args.dry_run)
    elif args.cmd == "run-one":
        run_one(args.code, args.mode, args.overwrite, args.dry_run)
    elif args.cmd == "status":
        status()
    elif args.cmd == "retry-failed":
        retry_failed()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
