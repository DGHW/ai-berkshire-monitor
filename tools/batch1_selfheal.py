#!/usr/bin/env python3
"""batch1_selfheal.py — batch1 中断自愈检查（17:00 定时触发）。

检测条件（全部满足才补跑 review）：
  1. pool 中存在 REVIEW_DUE 标的（有未处理的复核）
  2. agent.lock 不存在或已陈旧（>2h，说明 review 未在跑）
  3. 今天队列中没有 pending/in_progress 的 full 任务（没在排队）

补跑 = 调 agent_driver review --limit 1（幂等：无 REVIEW_DUE 时自然退出）。
用法：python3 tools/batch1_selfheal.py
退出码：0 无需补跑或补跑完成；1 补跑失败
"""

import json
import os
import subprocess
import sys
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL_FILE = os.path.join(REPO_ROOT, "data", "monitor", "pool.json")
QUEUE_FILE = os.path.join(REPO_ROOT, "data", "monitor", "agent_queue.json")
LOCK_FILE = os.path.join(REPO_ROOT, "data", "monitor", "agent.lock")
LOG_FILE = os.path.join(REPO_ROOT, "reports", "monitor", "daily", "cron.log")


def _log(msg: str):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [SELFHEAL] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _force_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    _force_utf8_stdio()
    # 1. REVIEW_DUE 数量
    try:
        with open(POOL_FILE, encoding="utf-8") as f:
            pool = json.load(f)
    except (OSError, json.JSONDecodeError):
        _log("pool.json 不可读，退出")
        return 0
    due = [c for c, s in pool.get("stocks", {}).items()
           if s.get("status") == "REVIEW_DUE"]
    add_due = [c for c, s in pool.get("stocks", {}).items()
               if s.get("status") == "ADD_DUE"]
    if not due and not add_due:
        _log("无 REVIEW_DUE/ADD_DUE 标的，无需补跑")
        return 0

    # 2. 锁检查（fresh 锁 = review 正在跑，不打扰）
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, encoding="utf-8") as f:
                lock = json.load(f)
            ts = datetime.fromisoformat(lock.get("ts", ""))
            age_min = (datetime.now() - ts).total_seconds() / 60
            if age_min < 120:
                _log(f"锁存在且新鲜（{age_min:.0f}min），review 进行中，不打扰")
                return 0
            _log(f"锁陈旧（{age_min:.0f}min），视为遗留，清除后补跑")
            os.remove(LOCK_FILE)
        except (ValueError, OSError, json.JSONDecodeError):
            pass

    # 3. 队列任务检查：清理僵死 in_progress（锁已陈旧时它们必死），跳过活跃任务
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        with open(QUEUE_FILE, encoding="utf-8") as f:
            q = json.load(f)
        q_dirty = False
        for job in q.get("jobs", []):
            if job.get("mode") != "full":
                continue
            if job.get("status") == "in_progress":
                # 锁已陈旧（前面已清除）→ in_progress 必为僵死，标 failed 释放
                _log(f"清理僵死 in_progress: {job['code']}（锁陈旧时仍标进行中=进程已死）")
                job["status"] = "failed"
                job["note"] = (job.get("note", "") + "; selfheal判定僵死").strip("; ")
                q_dirty = True
            elif job.get("status") == "pending" and \
                    job.get("enqueued_at", "").startswith(today):
                _log(f"{job['code']} 今日排队中，不重复补跑")
                return 0
        if q_dirty:
            with open(QUEUE_FILE, "w", encoding="utf-8") as f:
                json.dump(q, f, ensure_ascii=False, indent=1)
    except (OSError, json.JSONDecodeError):
        pass

    # 4. 补跑 review
    _log(f"检测到 REVIEW_DUE {len(due)} 只（{due}）+ ADD_DUE {len(add_due)} 只（{add_due}），补跑 review --limit 1")
    driver = os.path.join(REPO_ROOT, "tools", "agent_driver.py")
    r = subprocess.run([sys.executable, driver, "review", "--limit", "1"],
                       cwd=REPO_ROOT)
    if r.returncode == 0:
        _log("补跑 review 完成")
        return 0
    _log(f"补跑 review 失败（exit={r.returncode}）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
