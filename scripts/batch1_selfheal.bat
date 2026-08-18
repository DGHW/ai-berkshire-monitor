@echo off
REM ============================================================
REM  batch1 self-heal: check REVIEW_DUE leftover, auto re-run review
REM  schtasks: StockMonitorSelfHeal daily 17:00
REM ============================================================
cd /d C:\Users\17356\WorkBuddy\2026-08-07-20-15-31\ai-berkshire
set PY=C:\Users\17356\.workbuddy\binaries\python\envs\default\Scripts\python.exe
%PY% tools\batch1_selfheal.py
