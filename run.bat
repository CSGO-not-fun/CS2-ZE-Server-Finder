@echo off
setlocal EnableExtensions
cd /d "%~dp0" || (echo [ERROR] cd failed & pause & exit /b)
call run_core.bat
endlocal
