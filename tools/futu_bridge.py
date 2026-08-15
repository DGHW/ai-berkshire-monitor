#!/usr/bin/env python3
"""futu_bridge.py — 富途 OpenD 模拟盘执行层（AI Berkshire 买卖闭环 v6）。

封装官方 futuapi 技能脚本（data/futu_skills/ 或 ~/.codebuddy/skills/futuapi/），
为 agent_driver 提供模拟盘下单/查持仓/查资金能力。

**安全护栏（硬编码）**：
  - 交易环境固定 SIMULATE（模拟盘），代码中无 REAL 路径
  - futu 模拟盘失败绝不影响本地记账（agent_driver 捕获后仅告警）

用法：
    python3 tools/futu_bridge.py --check               # 环境检查（SDK/OpenD/账户）
    python3 tools/futu_bridge.py --buy CODE SHARES PRICE [--dry-run]  # 模拟盘限价买入
    python3 tools/futu_bridge.py --positions           # 模拟盘持仓（JSON）
    python3 tools/futu_bridge.py --cash                # 模拟盘资金（JSON）
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(REPO_ROOT, "data", "positions", "futu_config.json")

# 官方脚本位置（优先 ~/.codebuddy/skills，其次项目内 data/futu_skills）
SKILL_CANDIDATES = [
    os.path.join(os.path.expanduser("~"), ".codebuddy", "skills", "futuapi"),
    os.path.join(REPO_ROOT, "data", "futu_skills", "skills", "futuapi"),
]
TRADE_SCRIPTS = os.path.join("scripts", "trade")


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def find_skill_dir():
    for p in SKILL_CANDIDATES:
        if os.path.isdir(p):
            return p
    return None


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"enabled": True, "dry_run": True}


def map_code(code: str):
    """A股 6位代码 → 富途格式（SH./SZ./BJ.）。北交所默认跳过（返回 None）。
    规则：6xx/688 沪市；0/2/3 深市；4/8/920 北交所。"""
    code = code.strip()
    if code.startswith(("6", "9")):
        if code.startswith("920"):
            return f"BJ.{code}"  # 920 段为北交所新代码
        return f"SH.{code}"
    if code.startswith(("0", "2", "3")):
        return f"SZ.{code}"
    if code.startswith(("4", "8")):
        return f"BJ.{code}"
    return None


def run_skill_script(script: str, args: list) -> dict:
    """调用官方技能脚本。返回 {ok, stdout, stderr, json}。"""
    skill_dir = find_skill_dir()
    if not skill_dir:
        return {"ok": False, "error": "futuapi 技能未安装（~/.codebuddy/skills/futuapi 或 data/futu_skills）"}
    script_path = os.path.join(skill_dir, TRADE_SCRIPTS, script)
    if not os.path.exists(script_path):
        return {"ok": False, "error": f"脚本不存在: {script_path}"}
    cmd = [sys.executable, script_path] + args + ["--json"]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, timeout=120, env=env)
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        err = (proc.stderr or b"").decode("utf-8", errors="replace")
        parsed = None
        for line in reversed(out.strip().splitlines()):
            try:
                parsed = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        result = {"ok": proc.returncode == 0, "exit": proc.returncode,
                  "stdout": out, "stderr": err, "json": parsed}
        if not result["ok"]:
            _alert(f"{script} 失败(exit={proc.returncode}): {err[-300:]}")
        return result
    except subprocess.TimeoutExpired:
        _alert(f"{script} 超时(120s)")
        return {"ok": False, "error": "脚本超时(120s)"}


def _alert(msg: str):
    """告警日志：OpenD 掉线/下单失败等异常集中记录，batch1 日报可读。"""
    log_file = os.path.join(REPO_ROOT, "reports", "monitor", "futu_alerts.log")
    try:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass


def cmd_check():
    skill_dir = find_skill_dir()
    print(f"技能目录: {skill_dir or '未找到'}")
    # 1. SDK
    try:
        import futu
        print(f"SDK: futu-api {futu.__version__} ✅")
    except ImportError as e:
        print(f"SDK: futu-api 未安装 ({e}) ❌")
    # 2. OpenD 连通（11111 端口）
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", 11111))
        print("OpenD: 127.0.0.1:11111 连通 ✅")
    except Exception:
        print("OpenD: 127.0.0.1:11111 未连通 ❌（需安装并登录 OpenD）")
    finally:
        s.close()
    # 3. 账户列表（官方脚本）
    r = run_skill_script("get_accounts.py", [])
    if r["ok"]:
        print("账户查询 ✅")
        if r["json"]:
            print(json.dumps(r["json"], ensure_ascii=False, indent=1)[:800])
    else:
        print(f"账户查询 ❌: {r.get('stderr', r.get('error', ''))[:200]}")
    return 0


def _place_order(code: str, side: str, shares: int, price: float, remark: str, dry_run: bool) -> dict:
    """通用下单（BUY/SELL），返回结果 dict（不打印）。"""
    cfg = load_config()
    if not cfg.get("enabled", True):
        return {"ok": False, "reason": "futu 配置 disabled"}
    if dry_run or cfg.get("dry_run", True):
        return {"ok": True, "dry_run": True, "code": code, "side": side,
                "shares": shares, "price": price,
                "note": "dry-run（futu_config.dry_run=true）"}
    futu_code = map_code(code)
    if futu_code is None:
        return {"ok": False, "reason": f"代码 {code} 无法映射到富途（北交所/未知前缀）"}
    if futu_code.startswith("BJ."):
        return {"ok": False, "reason": f"北交所 {code} 模拟盘不支持"}
    args = ["--code", futu_code, "--side", side, "--quantity", str(shares),
            "--price", str(price), "--trd-env", "SIMULATE", "--remark", remark]
    acc_id = cfg.get("acc_id")
    if acc_id:
        args += ["--acc-id", str(acc_id)]
    r = run_skill_script("place_order.py", args)
    if r["ok"]:
        result = r["json"] or {}
        result["ok"] = True
        result["futu_code"] = futu_code
        return result
    return {"ok": False, "code": code, "futu_code": futu_code,
            "error": r.get("stderr", r.get("error", ""))[-400:]}


def cmd_buy(code: str, shares: int, price: float, dry_run: bool):
    print(json.dumps(_place_order(code, "BUY", shares, price, "ai-berkshire-auto", dry_run),
                     ensure_ascii=False))
    return 0


def cmd_sell(code: str, shares: int, price: float, dry_run: bool):
    print(json.dumps(_place_order(code, "SELL", shares, price, "ai-berkshire-auto", dry_run),
                     ensure_ascii=False))
    return 0


def cmd_orders():
    """查当日委托（含成交状态）。"""
    r = run_skill_script("get_orders.py", _acc_id_args())
    if r["ok"] and r["json"]:
        print(json.dumps(r["json"], ensure_ascii=False, indent=1))
    else:
        print(json.dumps({"ok": False, "error": r.get("stderr", r.get("error", ""))[-400:]}))
    return 0


def cmd_reconcile(auto_fix: bool = False):
    """成交核对：本地记账持仓 vs 模拟盘真实持仓/委托。
    输出差异清单：本地有而模拟盘无（挂单未成交）、模拟盘有而本地无（记账缺失）。
    """
    import sys as _sys
    _sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
    try:
        from position_manager import load_positions
    except Exception:
        # 直接读 positions.json
        def load_positions():
            p = os.path.join(REPO_ROOT, "data", "positions", "positions.json")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
            return {}
    local = load_positions()
    local_codes = {c for c, s in local.items() if s.get("shares", 0) > 0}

    r = run_skill_script("get_portfolio.py", _acc_id_args())
    futu_positions = {}
    if r["ok"] and r["json"]:
        for pos in r["json"].get("positions", []):
            code = str(pos.get("code", "")).split(".")[-1]
            futu_positions[code] = pos

    r2 = run_skill_script("get_orders.py", _acc_id_args())
    pending_orders = []
    if r2["ok"] and r2["json"]:
        for o in r2["json"].get("orders", []):
            if o.get("status") in ("SUBMITTED", "SUBMITTING", "WAITING"):
                pending_orders.append(o)

    issues = []
    # 1. 本地有、模拟盘无 → 挂单未成交（或下单失败）
    for c in local_codes:
        if c not in futu_positions:
            pend = [o for o in pending_orders if str(o.get("code", "")).split(".")[-1] == c]
            if pend:
                issues.append({"type": "UNFILLED", "code": c,
                               "local_shares": local[c]["shares"],
                               "pending_orders": pend,
                               "suggest": "挂单未成交：开盘高开或价格未触及。撤单后按现价重下或回滚记账"})
            else:
                issues.append({"type": "MISSING_FUTU", "code": c,
                               "local_shares": local[c]["shares"],
                               "suggest": "模拟盘无持仓且无挂单：下单失败或从未下单，需补下"})
    # 2. 模拟盘有、本地无
    for c, pos in futu_positions.items():
        if c not in local_codes:
            issues.append({"type": "MISSING_LOCAL", "code": c,
                           "futu_shares": pos.get("qty", pos.get("shares")),
                           "suggest": "模拟盘有持仓但本地未记账：补记账或模拟盘卖出"})

    # 3. 自动修复（--auto-fix）：UNFILLED/MISSING_FUTU → 撤单重下或回滚
    fixes = []
    if auto_fix:
        for issue in issues:
            if issue["type"] in ("UNFILLED", "MISSING_FUTU"):
                res = _auto_fix_unfilled(issue, local.get(issue["code"], {}))
                fixes.append({"code": issue["code"], "result": res})
    print(json.dumps({"ok": True, "issues": issues, "fixes": fixes,
                      "local": sorted(local_codes),
                      "futu": sorted(futu_positions.keys())}, ensure_ascii=False, indent=1))
    return 0


def decide_reorder(price: float, orig_price: float, zone_hi) -> str:
    """降级决策纯函数。返回 'downgrade'（低开观望）/ 'reorder'（重下）/ 'rollback'（超区回滚）。"""
    if orig_price is not None and price < orig_price:
        return "downgrade"
    if zone_hi is None or price <= zone_hi * 1.05:
        return "reorder"
    return "rollback"


def _auto_fix_unfilled(issue: dict, local_pos: dict) -> dict:
    """挂单未成交自动处理：撤单 → 分级决策（低开降级 / 击球区重下 / 超区回滚）。

    降级策略（开盘低开保护）：实时价低于原挂单价（市场走弱/低开）→ 不重下，
    回滚观望，价格企稳后由 price_monitor 按新价重新评估击球区自然触发。
    """
    code = issue["code"]
    local_shares = issue.get("local_shares", 0)
    pending = issue.get("pending_orders", [])
    orig_price = None
    for o in pending:
        if o.get("price") is not None:
            orig_price = float(o["price"])
            break
    # 1. 撤单
    for o in pending:
        oid = str(o.get("order_id", ""))
        if oid:
            r = run_skill_script("cancel_order.py", ["--order-id", oid] + _acc_id_args())
            if not r["ok"]:
                _alert(f"{code} 撤单失败 order={oid}")
    # 2. 实时价
    try:
        sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
        from quote_fetcher import get_spot
        spot = get_spot(code)
        price = float(spot["price"])
    except Exception:
        _alert(f"{code} 实时价获取失败，回滚记账")
        return _rollback_local(code, "实时价获取失败")
    # 3. 低开降级：实时价 < 原挂单价（市场走弱），回滚观望不接飞刀
    if decide_reorder(price, orig_price, None) == "downgrade":
        _alert(f"{code} 低开降级: 实时价 {price} < 挂单价 {orig_price}，回滚观望等企稳")
        return _rollback_local(code, f"低开降级: 实时价 {price} < 挂单价 {orig_price}，观望等企稳")
    pool_file = os.path.join(REPO_ROOT, "data", "monitor", "pool.json")
    zone_hi = None
    if os.path.exists(pool_file):
        with open(pool_file, encoding="utf-8") as f:
            pool = json.load(f)
        zone_hi = (pool.get("stocks", {}).get(code, {}).get("buy_zone") or {}).get("high")
    # 4. 击球区内（含 5% 容差）→ 按现价重下
    if decide_reorder(price, orig_price, zone_hi) == "reorder":
        res = _place_order(code, "BUY", int(local_shares), round(price, 2),
                           "ai-berkshire-autofix", dry_run=False)
        _alert(f"{code} auto-fix: 撤单后按现价 {price} 重下 {local_shares} 股 → {res.get('status', res.get('ok'))}")
        return {"action": "reorder", "price": price, "shares": local_shares,
                "order": res.get("order_id")}
    # 5. 超击球区 → 回滚记账
    return _rollback_local(code, f"现价 {price} 超击球区上沿 {zone_hi}，回滚")


def _rollback_local(code: str, reason: str) -> dict:
    """回滚本地记账：positions 清零 + pool 状态回 WATCHING。"""
    pos_file = os.path.join(REPO_ROOT, "data", "positions", "positions.json")
    if os.path.exists(pos_file):
        with open(pos_file, encoding="utf-8") as f:
            positions = json.load(f)
        if code in positions:
            positions[code]["shares"] = 0
            positions[code]["sell_reason"] = f"auto-rollback: {reason}"
            positions[code]["sell_date"] = datetime.now().strftime("%Y-%m-%d")
            with open(pos_file, "w", encoding="utf-8") as f:
                f.write(json.dumps(positions, ensure_ascii=False, indent=1) + "\n")
    pool_file = os.path.join(REPO_ROOT, "data", "monitor", "pool.json")
    if os.path.exists(pool_file):
        with open(pool_file, encoding="utf-8") as f:
            pool = json.load(f)
        if code in pool.get("stocks", {}):
            pool["stocks"][code]["status"] = "WATCHING"
            pool["stocks"][code]["note"] = f"auto-rollback: {reason}"
            pool["updated"] = datetime.now().strftime("%Y-%m-%d")
            with open(pool_file, "w", encoding="utf-8") as f:
                f.write(json.dumps(pool, ensure_ascii=False, indent=1) + "\n")
    _alert(f"{code} 回滚记账: {reason}")
    return {"action": "rollback", "reason": reason}


def _acc_id_args():
    cfg = load_config()
    acc_id = cfg.get("acc_id")
    return ["--acc-id", str(acc_id)] if acc_id else []


def cmd_positions():
    r = run_skill_script("get_portfolio.py", _acc_id_args())
    if r["ok"] and r["json"]:
        print(json.dumps(r["json"], ensure_ascii=False, indent=1))
    else:
        print(json.dumps({"ok": False, "error": r.get("stderr", r.get("error", ""))[-400:]}))
    return 0


def cmd_cash():
    # 模拟账户不支持现金流水；get_portfolio 返回 funds（total_assets/cash/avl）即资金快照
    r = run_skill_script("get_portfolio.py", _acc_id_args())
    if r["ok"] and r["json"]:
        print(json.dumps(r["json"].get("funds", r["json"]), ensure_ascii=False, indent=1))
    else:
        print(json.dumps({"ok": False, "error": r.get("stderr", r.get("error", ""))[-400:]}))
    return 0


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="富途 OpenD 模拟盘执行层")
    parser.add_argument("--check", action="store_true", help="环境检查")
    parser.add_argument("--buy", nargs=3, metavar=("CODE", "SHARES", "PRICE"), help="模拟盘限价买入")
    parser.add_argument("--sell", nargs=3, metavar=("CODE", "SHARES", "PRICE"), help="模拟盘限价卖出")
    parser.add_argument("--dry-run", action="store_true", help="仅预览不下单")
    parser.add_argument("--positions", action="store_true", help="查模拟盘持仓")
    parser.add_argument("--orders", action="store_true", help="查当日委托")
    parser.add_argument("--reconcile", action="store_true", help="成交核对（本地 vs 模拟盘）")
    parser.add_argument("--auto-fix", action="store_true", help="核对后自动修复（撤单重下/回滚记账）")
    parser.add_argument("--cash", action="store_true", help="查模拟盘资金")
    args = parser.parse_args()

    if args.check:
        return cmd_check()
    if args.buy:
        code, shares, price = args.buy
        return cmd_buy(code, int(shares), float(price), args.dry_run)
    if args.sell:
        code, shares, price = args.sell
        return cmd_sell(code, int(shares), float(price), args.dry_run)
    if args.positions:
        return cmd_positions()
    if args.orders:
        return cmd_orders()
    if args.reconcile:
        return cmd_reconcile(auto_fix=args.auto_fix)
    if args.cash:
        return cmd_cash()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
