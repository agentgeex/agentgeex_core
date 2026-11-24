@echo off
:: Worker node install entry (Windows)
::
:: Install:
:: Interactive install: agx_host.bat
:: Non-interactive, all defaults: agx_host.bat --default
:: Preview mode: agx_host.bat --dry-run
:: Custom working directory: agx_host.bat --workdir DIR
:: Force overwrite: agx_host.bat --force
::
:: Uninstall:
::   agx_host.bat --uninstall  uninstall agx_host on this machine
::
:: Wakeup:
::   agx_host.bat --wakeup     wake up the local agx_host

setlocal enabledelayedexpansion

:: ── force UTF-8 output ──
set PYTHONUTF8=1
cd /d "%~dp0"

:: back up the externally supplied arguments
set "RAW_ARGS=%*"

:: install (config is read from agx_config.json)
uv run python install.py --host !RAW_ARGS!

endlocal