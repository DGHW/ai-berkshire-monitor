@echo off
REM ============================================================
REM  买卖闭环定时任务 — 半月/月度 batch2（轮动 + 基准刷新）
REM  Windows 计划任务：schtasks /create /tn "StockRotationBiweekly" /tr "cmd /c C:\Users\17356\WorkBuddy\2026-08-07-20-15-31\ai-berkshire\scripts\batch2_rotate.bat" /sc monthly /d 1,15 /st 09:05
REM ============================================================
cd /d C:\Users\17356\WorkBuddy\2026-08-07-20-15-31\ai-berkshire
set PY=C:\Users\17356\.workbuddy\binaries\python\envs\default\Scripts\python.exe

echo [%date% %time%] ==== batch2 轮动开始 ====
%PY% tools\pool_rotator.py --monthly-rotate  >> reports\monitor\cron_rotation.log 2>&1
%PY% tools\pool_kelly.py --refresh-basis     >> reports\monitor\cron_rotation.log 2>&1
%PY% tools\position_manager.py --rotate      >> reports\monitor\cron_rotation.log 2>&1
%PY% tools\pool_rotator.py --report          >> reports\monitor\cron_rotation.log 2>&1
echo [%date% %time%] ==== batch2 完成 ====
