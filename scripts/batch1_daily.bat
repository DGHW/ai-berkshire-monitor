@echo off
REM ============================================================
REM  买卖闭环定时任务 — 每日 batch1（价格扫描 + 持仓巡检 + 自动深度复核买入）
REM  Windows 计划任务：schtasks /create /tn "StockMonitorDaily" /tr "cmd /c C:\Users\17356\WorkBuddy\2026-08-07-20-15-31\ai-berkshire\scripts\batch1_daily.bat" /sc daily /st 15:05 /ru "%USERNAME%" /rl LIMITED /f
REM ============================================================
cd /d C:\Users\17356\WorkBuddy\2026-08-07-20-15-31\ai-berkshire
set PY=C:\Users\17356\.workbuddy\binaries\python\envs\default\Scripts\python.exe

echo [%date% %time%] ==== 每日 batch1 开始 ====
%PY% tools\price_monitor.py            >> reports\monitor\daily\cron.log 2>&1
%PY% tools\position_manager.py --daily >> reports\monitor\daily\cron.log 2>&1
%PY% tools\pool_rotator.py --health-only >> reports\monitor\daily\cron.log 2>&1
REM 自动复核：REVIEW_DUE → 完整重研 → 六道闸门 → 自动建仓（默认每日 1 只，多只排队）
%PY% tools\agent_driver.py review --limit 1 >> reports\monitor\cron_agent.log 2>&1
echo [%date% %time%] ==== batch1 完成 ====
