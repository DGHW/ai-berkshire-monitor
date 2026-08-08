@echo off
REM 批量速评调度器入口（确保使用 venv python，因为系统 python 缺 tushare）
REM 用法: batch_review.bat prepare --pool small --batch 1
"C:\Users\17356\.workbuddy\binaries\python\envs\default\Scripts\python.exe" "%~dp0..\tools\batch_review.py" %*
