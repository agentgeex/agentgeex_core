@echo off
REM agx.bat — AgentGeeX unified entry point (Windows)
REM
REM Install:
REM   agx.bat                  core node (daemon + watchdog + ws)
REM   agx.bat --core           same as above, explicit
REM   agx.bat --host           worker node (daemon + ws, lightweight install)
REM   agx.bat --default        non-interactive, all defaults
REM   agx.bat --dry-run        preview mode
REM   agx.bat --workdir DIR    custom working directory
REM   agx.bat --force          force overwrite
REM
REM Uninstall:
REM   agx.bat --uninstall      full uninstall
REM
REM Wakeup:
REM   agx.bat --wakeup         wake up agx_core

setlocal
cd /d "%~dp0"

uv run python install.py %*
