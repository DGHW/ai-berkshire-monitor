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
        return {"ok": proc.returncode == 0, "exit": proc.returncode,
                "stdout": out, "stderr": err, "json": parsed}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "脚本超时(120s)"}


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


def cmd_reconcile():
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
    print(json.dumps({"ok": True, "issues": issues,
                      "local": sorted(local_codes),
                      "futu": sorted(futu_positions.keys())}, ensure_ascii=False, indent=1))
    return 0


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
        return cmd_reconcile()
    if args.cash:
        return cmd_cash()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
