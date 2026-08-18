@echo off
REM ============================================================
REM  Buy/Sell loop batch1 (daily): price scan + position check + auto review/buy
REM  schtasks: StockMonitorDaily daily 15:05
REM  Heartbeat: every step writes [STEP] markers into cron.log
REM ============================================================
cd /d C:\Users\17356\WorkBuddy\2026-08-07-20-15-31\ai-berkshire
set PY=C:\Users\17356\.workbuddy\binaries\python\envs\default\Scripts\python.exe
set LOG=reports\monitor\daily\cron.log

echo [%date% %time%] [STEP] batch1 start >> %LOG%
echo [%date% %time%] [STEP] price_monitor start >> %LOG%
%PY% tools\price_monitor.py             >> %LOG% 2>&1
echo [%date% %time%] [STEP] price_monitor done >> %LOG%
echo [%date% %time%] [STEP] position_manager.daily start >> %LOG%
%PY% tools\position_manager.py --daily  >> %LOG% 2>&1
echo [%date% %time%] [STEP] position_manager.daily done >> %LOG%
echo [%date% %time%] [STEP] health_check start >> %LOG%
%PY% tools\pool_rotator.py --health-only >> %LOG% 2>&1
echo [%date% %time%] [STEP] health_check done >> %LOG%
echo [%date% %time%] [STEP] futu_reconcile start >> %LOG%
%PY% tools\futu_bridge.py --reconcile   >> %LOG% 2>&1
echo [%date% %time%] [STEP] futu_reconcile done >> %LOG%
echo [%date% %time%] [STEP] agent_review start >> %LOG%
%PY% tools\agent_driver.py review --limit 1 >> reports\monitor\cron_agent.log 2>&1
echo [%date% %time%] [STEP] agent_review done >> %LOG%
echo [%date% %time%] [STEP] batch1 done >> %LOG%
