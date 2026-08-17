@echo off
REM ============================================================
REM  Buy/Sell loop batch1 (daily): price scan + position check + auto review/buy
REM  schtasks: StockMonitorDaily daily 15:05
REM ============================================================
cd /d C:\Users\17356\WorkBuddy\2026-08-07-20-15-31\ai-berkshire
set PY=C:\Users\17356\.workbuddy\binaries\python\envs\default\Scripts\python.exe

echo [%date% %time%] ==== batch1 start ====
%PY% tools\price_monitor.py             >> reports\monitor\daily\cron.log 2>&1
%PY% tools\position_manager.py --daily  >> reports\monitor\daily\cron.log 2>&1
%PY% tools\pool_rotator.py --health-only >> reports\monitor\daily\cron.log 2>&1
REM futu reconcile: local vs sim positions diff
%PY% tools\futu_bridge.py --reconcile   >> reports\monitor\daily\cron.log 2>&1
REM auto review: REVIEW_DUE -> full re-research -> 6 gates -> auto buy (1/day)
%PY% tools\agent_driver.py review --limit 1 >> reports\monitor\cron_agent.log 2>&1
echo [%date% %time%] ==== batch1 done ====
