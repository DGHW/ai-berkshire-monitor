@echo off
REM ============================================================
REM  ???????? ? ??/?? batch2??????? + ?? + ?????
REM  Windows ?????schtasks /create /tn "StockRotationBiweekly" /tr "cmd /c C:\Users\17356\WorkBuddy\2026-08-07-20-15-31\ai-berkshire\scripts\batch2_rotate.bat" /sc monthly /d 1,15 /st 09:05 /ru "%USERNAME%" /rl LIMITED /f
REM ============================================================
cd /d C:\Users\17356\WorkBuddy\2026-08-07-20-15-31\ai-berkshire
set PY=C:\Users\17356\.workbuddy\binaries\python\envs\default\Scripts\python.exe

echo [%date% %time%] ==== batch2 ???? ====
REM ? ??????????????? + ?? lite ?? ? report_sync ???P2: cap 30?60 ??????
%PY% tools\agent_driver.py batch2-research --lite-cap 60 >> reports\monitor\cron_agent.log 2>&1
REM ? ??????????????
%PY% tools\pool_rotator.py --monthly-rotate  >> reports\monitor\cron_rotation.log 2>&1
%PY% tools\pool_kelly.py --refresh-basis     >> reports\monitor\cron_rotation.log 2>&1
%PY% tools\position_manager.py --rotate      >> reports\monitor\cron_rotation.log 2>&1
%PY% tools\pool_rotator.py --report          >> reports\monitor\cron_rotation.log 2>&1
echo [%date% %time%] ==== batch2 ?? ====
