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


def cmd_buy(code: str, shares: int, price: float, dry_run: bool):
    cfg = load_config()
    if not cfg.get("enabled", True):
        print(json.dumps({"ok": False, "reason": "futu 配置 disabled"}))
        return 0
    if dry_run or cfg.get("dry_run", True):
        print(json.dumps({"ok": True, "dry_run": True,
                          "code": code, "shares": shares, "price": price,
                          "note": "dry-run（futu_config.dry_run=true）"}))
        return 0
    futu_code = map_code(code)
    if futu_code is None:
        print(json.dumps({"ok": False, "reason": f"代码 {code} 无法映射到富途（北交所/未知前缀），跳过模拟下单"}))
        return 0
    if futu_code.startswith("BJ."):
        print(json.dumps({"ok": False, "reason": f"北交所 {code} 模拟盘不支持，跳过"}))
        return 0
    args = ["--code", futu_code, "--side", "BUY", "--quantity", str(shares),
            "--price", str(price), "--trd-env", "SIMULATE", "--remark", "ai-berkshire-auto"]
    r = run_skill_script("place_order.py", args)
    if r["ok"]:
        result = r["json"] or {}
        result["ok"] = True
        result["futu_code"] = futu_code
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps({"ok": False, "code": code, "futu_code": futu_code,
                          "error": r.get("stderr", r.get("error", ""))[-400:]}))
    return 0


def cmd_positions():
    r = run_skill_script("get_portfolio.py", [])
    if r["ok"] and r["json"]:
        print(json.dumps(r["json"], ensure_ascii=False, indent=1))
    else:
        print(json.dumps({"ok": False, "error": r.get("stderr", r.get("error", ""))[-400:]}))
    return 0


def cmd_cash():
    r = run_skill_script("get_acc_cash_flow.py", [])
    if r["ok"] and r["json"]:
        print(json.dumps(r["json"], ensure_ascii=False, indent=1))
    else:
        # 兜底用 get_accounts 的资产信息
        r2 = run_skill_script("get_accounts.py", [])
        if r2["ok"]:
            print(json.dumps(r2["json"], ensure_ascii=False, indent=1))
        else:
            print(json.dumps({"ok": False, "error": r2.get("stderr", r2.get("error", ""))[-400:]}))
    return 0


def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="富途 OpenD 模拟盘执行层")
    parser.add_argument("--check", action="store_true", help="环境检查")
    parser.add_argument("--buy", nargs=3, metavar=("CODE", "SHARES", "PRICE"), help="模拟盘限价买入")
    parser.add_argument("--dry-run", action="store_true", help="仅预览不下单")
    parser.add_argument("--positions", action="store_true", help="查模拟盘持仓")
    parser.add_argument("--cash", action="store_true", help="查模拟盘资金")
    args = parser.parse_args()

    if args.check:
        return cmd_check()
    if args.buy:
        code, shares, price = args.buy
        return cmd_buy(code, int(shares), float(price), args.dry_run)
    if args.positions:
        return cmd_positions()
    if args.cash:
        return cmd_cash()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
