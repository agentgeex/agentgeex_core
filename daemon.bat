@echo off
:: ============================================================================
:: daemon.bat — AgentGeeX daemon start/stop/status/restart/log management script (Windows)
::
:: Usage:
::   daemon.bat start           start the daemon (core mode by default)
::   daemon.bat start --host    start in host mode (daemon + ws only, no watchdog)
::   daemon.bat stop            stop the daemon
::   daemon.bat stop --force    force stop
::   daemon.bat status          show running state
::   daemon.bat restart         restart (core mode by default)
::   daemon.bat restart --host  restart in host mode
::   daemon.bat logs            show the tail of the log
::
:: Process management: PID file + window title
:: Log file: logs\{HOST_ID}.out
:: Config file: agx_config.json
:: ============================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"
set PYTHONUTF8=1

:: ── read the config from agx_config.json ──────────────
set HOST_ID=
set NETWORK_ID=
set CONFIG_FILE=%~dp0agx_config.json
if exist "%CONFIG_FILE%" (
    for /f "usebackq delims=" %%a in (`python -c "import json;f=open(r'%CONFIG_FILE%');c=json.load(f);print(c.get('host_id',''))" 2^>nul`) do set HOST_ID=%%a
    for /f "usebackq delims=" %%a in (`python -c "import json;f=open(r'%CONFIG_FILE%');c=json.load(f);print(c.get('network_id',''))" 2^>nul`) do set NETWORK_ID=%%a
)
if "%HOST_ID%"=="" set HOST_ID=%AO_HOST_ID%
if "%HOST_ID%"=="" for /f "tokens=*" %%a in ('hostname') do set HOST_ID=%%a
for /f "tokens=*" %%a in ('hostname') do set HOSTNAME_SHORT=%%a

set SESSION_TITLE=AgentGeeX-%HOST_ID%
set PID_FILE=logs\%HOST_ID%.pid
set LOG_FILE=logs\%HOST_ID%.out
if not exist logs mkdir logs

:: ── parse arguments ──────────────────────────────────
set DAEMON_MODE=--core
set FORCE_STOP=0
:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="--core"  set DAEMON_MODE=--core
if /i "%~1"=="--host"  set DAEMON_MODE=--host
if /i "%~1"=="--force" set FORCE_STOP=1
shift
goto :parse_args
:args_done

:: ── check whether the process is alive ────────────────
set PID_ALIVE=0
if exist "%PID_FILE%" (
    set /p SAVED_PID=<"%PID_FILE%"
    tasklist /FI "PID eq %SAVED_PID%" /FO LIST 2>nul | find "PID:" >nul
    if not errorlevel 1 set PID_ALIVE=1
)

:: ── status ─────────────────────────────────────────────────
if /i "%~1"=="status" goto :do_status
if /i "%~1"=="start"   goto :do_start
if /i "%~1"=="stop"    goto :do_stop
if /i "%~1"=="restart" goto :do_restart
if /i "%~1"=="logs"    goto :do_logs
if /i "%~1"=="" goto :usage
goto :usage

:do_status
if "%PID_ALIVE%"=="1" (
    echo ✅ 守护进程运行中 (PID=%SAVED_PID%)
    echo    窗口标题: %SESSION_TITLE%
    echo    日志文件: %LOG_FILE%
    exit /b 0
) else (
    echo ⏸  守护进程未运行
    if exist "%PID_FILE%" del "%PID_FILE%" 2>nul
    exit /b 1
)

:: ── start ──────────────────────────────────────────────────
:do_start
:: if it is already running, stop it first
if "%PID_ALIVE%"=="1" (
    echo ⚠️  守护进程已在运行 (PID=%SAVED_PID%)，自动重启中...
    call :kill_process
    timeout /t 1 /nobreak >nul
)

echo 本机: %HOSTNAME_SHORT%
echo network_id: %NETWORK_ID%
echo host_id:    %HOST_ID%
echo 运行模式:  %DAEMON_MODE:~2%
echo.

:: ── generate a temporary launch script ────────────────
set LAUNCHER=%TEMP%\_agx_daemon_%HOST_ID%.bat
> "%LAUNCHER%" (
    echo @echo off
    echo title %SESSION_TITLE%
    echo cd /d "%CD%"
    echo set PYTHONUTF8=1
    echo set PYTHONUNBUFFERED=1
    echo echo 本机: %HOSTNAME_SHORT%
    echo echo network_id: %NETWORK_ID%
    echo echo host_id: %HOST_ID%
    echo echo 模式: %DAEMON_MODE:~2%
    echo echo.
    echo uv run python scripts\agx_daemon.py %DAEMON_MODE%
)

echo 启动守护进程 (新窗口)...
echo   窗口标题: %SESSION_TITLE%
echo   运行模式: %DAEMON_MODE:~2%

start "%SESSION_TITLE%" cmd /k "%LAUNCHER%"

:: ── wait and save the PID ─────────────────────────────
timeout /t 3 /nobreak >nul
for /f "tokens=2" %%a in ('tasklist /FI "WINDOWTITLE eq %SESSION_TITLE%" /FO LIST 2^>nul ^| find "PID:"') do (
    echo %%a > "%PID_FILE%"
)

if exist "%PID_FILE%" (
    set /p NEW_PID=<"%PID_FILE%"
    echo.
    echo ✅ 守护进程启动成功 (PID=%NEW_PID%)
    echo    日志文件: %LOG_FILE%
    echo    停止: daemon.bat stop
    exit /b 0
) else (
    echo ❌ 守护进程启动失败
    echo    请检查日志: %LOG_FILE%
    exit /b 1
)

:: ── stop ───────────────────────────────────────────────────
:do_stop
if "%PID_ALIVE%"=="0" (
    echo ⏸  守护进程未运行
    if exist "%PID_FILE%" del "%PID_FILE%" 2>nul
    exit /b 0
)

echo 🛑 停止守护进程 (PID=%SAVED_PID%)...

if "%FORCE_STOP%"=="1" goto :force_kill

:: graceful stop: send SIGTERM first (taskkill without /F)
taskkill /PID %SAVED_PID% /T >nul 2>&1
echo    等待退出 (最多 10 秒)...
for /l %%i in (1,1,10) do (
    tasklist /FI "PID eq %SAVED_PID%" /FO LIST 2>nul | find "PID:" >nul
    if errorlevel 1 goto :stopped_ok
    timeout /t 1 /nobreak >nul
)

:: timed out, terminate forcibly
:force_kill
echo    超时未退出，强制终止...
taskkill /PID %SAVED_PID% /T /F >nul 2>&1

:stopped_ok
echo ✅ 守护进程已停止
del "%PID_FILE%" 2>nul
exit /b 0

:: ── kill_process (called internally) ──────────────────
:kill_process
taskkill /PID %SAVED_PID% /T /F >nul 2>&1
del "%PID_FILE%" 2>nul
set PID_ALIVE=0
goto :eof

:: ── restart ────────────────────────────────────────────────
:do_restart
call :do_stop
timeout /t 1 /nobreak >nul
goto :do_start

:: ── logs ───────────────────────────────────────────────────
:do_logs
if exist "%LOG_FILE%" (
    type "%LOG_FILE%" | more
) else (
    echo 日志文件不存在: %LOG_FILE%
)
exit /b 0

:: ── usage ──────────────────────────────────────────────────
:usage
echo 用法: %0 {start [--core^|--host] ^| stop [--force] ^| status ^| restart ^| logs}
exit /b 1
