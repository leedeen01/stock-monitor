@echo off
REM Entry point for Windows Task Scheduler.
REM
REM Paths resolve from this file's own location (%~dp0) rather than the working
REM directory, because Task Scheduler starts jobs in system32 unless told
REM otherwise. daily_update.py writes its own log under data\logs\.

setlocal
set ROOT=%~dp0

cd /d "%ROOT%ingest" || exit /b 1
"%ROOT%.venv\Scripts\python.exe" daily_update.py

REM 0 = clean, 1 = ran with per-ticker failures, 2 = crashed.
exit /b %ERRORLEVEL%
