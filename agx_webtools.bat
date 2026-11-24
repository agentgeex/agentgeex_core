@echo off
setlocal enabledelayedexpansion
REM ──────────────────────────────────────────────
REM AgentGeeX WebTools start/stop script (Windows)
REM Usage:
REM   agx_webtools.bat --start   -> start the Streamlit Dashboard in the background (8501)
REM   agx_webtools.bat --stop    -> stop every webtools process
REM   agx_webtools.bat --status  -> show running state
REM   agx_webtools.bat --restart -> restart
REM ──────────────────────────────────────────────

set SCRIPT_DIR=%~dp0
for %%I in ("%SCRIPT_DIR%..") do set PROJECT_DIR=%%~fI
set APP_DIR=%PROJECT_DIR%\webtools
set LOG_DIR=%PROJECT_DIR%\logs\webtools
set PID_FILE=%LOG_DIR%\dashboard.pid
set PORT=8501

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM ── dispatch by argument ──
if "%~1"=="" goto :usage
if /i "%~1"=="--start"   goto :do_start
if /i "%~1"=="--stop"    goto :do_stop
if /i "%~1"=="--status"  goto :do_status
if /i "%~1"=="--restart" goto :do_restart
goto :usage

REM ── resolve the PID ──
:get_pid
set _PID=
if exist "%PID_FILE%" (
    set /p _PID=<"%PID_FILE%"
) else (
    set _PID=
)
goto :eof

REM ── status ──
:do_status
call :get_pid
if defined _PID (
    tasklist /FI "PID eq !_PID!" 2>NUL | find "!_PID!" >NUL
    if !errorlevel!==0 (
        echo [OK] Streamlit Dashboard 运行中
        echo    PID: !_PID!
        echo    URL: http://localhost:%PORT%
        echo    日志: %LOG_DIR%\dashboard.log
        goto :eof
    )
)
echo [--] Streamlit Dashboard 未运行
goto :eof

REM ── start ──
:do_start
call :get_pid
if defined _PID (
    tasklist /FI "PID eq !_PID!" 2>NUL | find "!_PID!" >NUL
    if !errorlevel!==0 (
        echo [!!] Dashboard 已在运行 ^(PID: !_PID!^)
        echo    先用 --stop 停止，或用 --restart 重启
        goto :eof
    )
)

echo [>>] 启动 AgentGeeX Dashboard...
cd /d "%PROJECT_DIR%"

REM launch in the background with start /b; the pid is resolved via powershell
start /b uv run streamlit run "%APP_DIR%\app.py" --server.port %PORT% --server.headless true --server.address 0.0.0.0 --browser.gatherUsageStats false > "%LOG_DIR%\dashboard.log" 2>&1

REM resolve the streamlit process PID (wait briefly so it can start)
timeout /t 3 /nobreak >NUL
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq streamlit.exe" /FO TABLE /NH 2^>NUL ^| find "streamlit"') do (
    echo %%a>"%PID_FILE%"
    goto :pid_ok
)
REM if streamlit.exe is not found, try python
for /f "tokens=2" %%a in ('tasklist /FI "WINDOWTITLE eq *streamlit*" /FO TABLE /NH 2^>NUL') do (
    echo %%a>"%PID_FILE%"
    goto :pid_ok
)

:pid_ok
call :get_pid
if defined _PID (
    echo [OK] Dashboard 已启动
    echo    PID: !_PID!
    echo    URL: http://localhost:%PORT%
    echo    日志: %LOG_DIR%\dashboard.log
) else (
    echo [ERR] 启动失败，查看日志: %LOG_DIR%\dashboard.log
    del "%PID_FILE%" 2>NUL
)
goto :eof

REM ── stop ──
:do_stop
call :get_pid
if defined _PID (
    tasklist /FI "PID eq !_PID!" 2>NUL | find "!_PID!" >NUL
    if !errorlevel!==0 (
        echo [<<] 停止 Dashboard ^(PID: !_PID!^)...
        taskkill /PID !_PID! /F 2>NUL
        timeout /t 1 /nobreak >NUL
    )
    del "%PID_FILE%" 2>NUL
)

REM fallback: kill whatever is left on port 8501
for /f "tokens=5" %%a in ('netstat -ano ^| find ":%PORT% " ^| find "LISTENING" 2^>NUL') do (
    taskkill /PID %%a /F 2>NUL
)
echo [OK] Dashboard 已停止
goto :eof

REM ── restart ──
:do_restart
call :do_stop
timeout /t 2 /nobreak >NUL
call :do_start
goto :eof

REM ── help ──
:usage
echo 用法: %~nx0 [--start^|--stop^|--status^|--restart]
echo.
echo   --start   后台启动 Streamlit Dashboard ^(端口 %PORT%^)
echo   --stop    停止 Dashboard
echo   --status  查看运行状态
echo   --restart 重启 Dashboard
goto :eof
