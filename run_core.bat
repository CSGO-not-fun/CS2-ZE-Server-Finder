@echo off
setlocal EnableExtensions
rem —— 固定到脚本所在目录，避免路径空格/中文影响 ——
cd /d "%~dp0"

rem —— 强制使用系统自带的 cmd，并在子命令里禁用 AutoRun ——
set "ComSpec=%SystemRoot%\System32\cmd.exe"

rem ===== Quiet mode =====
set "TRACE=0"

rem ===== Config =====
set "PROJ=%~dp0"
set "OUT=%PROJ%servers_output.csv"
set "EXITCODE=0"
set "START_WEB=1"
set "RUN_WARMUP=1"

rem ===== Pick Python from env (no health checks) =====
if defined PYTHON_EXE (
  set "SYSTEM_PY=%PYTHON_EXE%"
) else if defined PYTHON_HOME (
  set "SYSTEM_PY=%PYTHON_HOME%\python.exe"
) else (
  set "SYSTEM_PY=python"
)

rem ===== Writable temp =====
set "TMPBASE=%PROJ%_tmp"
if not exist "%TMPBASE%" mkdir "%TMPBASE%" >nul 2>&1
set "TEMP=%TMPBASE%"
set "TMP=%TMPBASE%"

call :DBG "START run_core, PY=%SYSTEM_PY%"

rem ===== Ensure venv =====
call :EnsureVenv || (
  echo [ERROR] Create virtual environment failed.
  set EXITCODE=1
  goto END
)
set "PY=%PROJ%venv\Scripts\python.exe"

rem ===== Dependencies: only if really needed =====
call :NeedInstall "%PY%" NEED
if defined NEED (
  echo [INFO] Installing/Updating dependencies...
  if exist "%PROJ%requirements.txt" (
    "%PY%" -m pip install --disable-pip-version-check -q -r "%PROJ%requirements.txt" || (
      echo [ERROR] pip -r requirements.txt failed.
      set EXITCODE=1
      goto END
    )
  ) else (
    "%PY%" -m pip install --disable-pip-version-check -q python-a2s==1.4.0 aiohttp flask async-timeout || (
      echo [ERROR] Minimal dependencies install failed.
      set EXITCODE=1
      goto END
    )
  )
) else (
  echo [INFO] Dependencies already satisfied. Skipping pip.
)

rem ===== Ensure server_list.txt =====
if not exist "%PROJ%server_list.txt" (
  > "%PROJ%server_list.txt" echo # IP:PORT ^| Name
  >>"%PROJ%server_list.txt" echo 74.91.124.21:27015 ^| Example Name
)

rem ===== Warm-up (optional) =====
if "%RUN_WARMUP%"=="1" call :DoWarmup

rem ===== Start web viewer (quiet, no path noise) =====
if "%START_WEB%"=="1" (
  if exist "%PROJ%web_view.py" (
    set "HAS5000="
    set "HAS5001="
    call :CheckPort 5000 HAS5000
    call :CheckPort 5001 HAS5001
    if not defined HAS5000 if not defined HAS5001 (
      rem 用短命 cmd 禁用 AutoRun，静默启动 web_view
      start "" "%ComSpec%" /d /c ""%PY%" "%PROJ%web_view.py"" 1>nul 2>nul
    )
  )
)

goto END


rem ================== FUNCTIONS ==================
:DBG
if "%TRACE%"=="1" echo [TRACE] %~1
exit /b 0

:EnsureVenv
if exist "%PROJ%venv\Scripts\python.exe" exit /b 0
if exist "%PROJ%venv" if not exist "%PROJ%venv\Scripts\python.exe" (
  echo [WARN] Broken venv detected. Removing and recreating...
  rmdir /s /q "%PROJ%venv"
)
set "tries=0"
:mkvenv_try
set /a tries+=1
"%SYSTEM_PY%" -m venv "%PROJ%venv"
if exist "%PROJ%venv\Scripts\python.exe" exit /b 0
if %tries% GEQ 2 exit /b 1
ping -n 3 127.0.0.1 >nul
goto :mkvenv_try

rem NeedInstall <pyExe> -> sets %2=1 when any spec missing/mismatch; clears %2 otherwise.
:NeedInstall
setlocal EnableDelayedExpansion
set "PYE=%~1"
set "REQFILE=%PROJ%requirements.txt"
set "NEED="

if exist "%REQFILE%" (
  for /f "usebackq tokens=1 delims=#" %%R in ("%REQFILE%") do (
    set "LINE=%%R"
    if not "!LINE!"=="" (
      for /f "tokens=1 delims=>=<! " %%P in ("!LINE!") do set "PK=%%P"
      echo !LINE! | find "==" >nul
      if errorlevel 1 (
        "%PYE%" -m pip show "!PK!" >nul 2>&1 || ( set "NEED=1" & goto :done )
      ) else (
        for /f "tokens=1,2 delims==" %%A in ("!LINE!") do ( set "PN=%%A" & set "PV=%%B" )
        for /f "usebackq tokens=2 delims=:" %%V in (`
          "%ComSpec%" /d /c ""%PYE%" -m pip show "!PN!" 2^>nul ^| find /I "Version:""
        `) do set "CUR=%%~V"
        if "!CUR!"=="" ( set "NEED=1" & goto :done )
        for /f "tokens=* delims= " %%Z in ("!CUR!") do set "CUR=%%Z"
        if /I not "!CUR!"=="!PV!" ( set "NEED=1" & goto :done )
      )
    )
  )
) else (
  for %%P in (python-a2s==1.4.0,aiohttp,flask,async-timeout) do (
    for /f "tokens=1,2 delims==" %%A in ("%%P") do (
      set "PN=%%A" & set "PV=%%B"
      "%PYE%" -m pip show "!PN!" >nul 2>&1 || ( set "NEED=1" & goto :done )
      if defined PV (
        for /f "usebackq tokens=2 delims=:" %%V in (`
          "%ComSpec%" /d /c ""%PYE%" -m pip show "!PN!" 2^>nul ^| find /I "Version:""
        `) do set "CUR=%%~V"
        for /f "tokens=* delims= " %%Z in ("!CUR!") do set "CUR=%%Z"
        if /I not "!CUR!"=="!PV!" ( set "NEED=1" & goto :done )
      )
    )
  )
)

:done
endlocal & ( if defined NEED ( set "%~2=1" ) else ( set "%~2=" ) )
exit /b 0

:CheckPort
setlocal
set "PORT=%~1"
set "FLAG="

rem 把 netstat 输出到临时文件再查，避免 ::%PORT% 被误解析成“注释/标签”
set "NS_TMP=%TMP%\netstat_%PORT%_%RANDOM%.txt"
"%ComSpec%" /d /c "netstat -ano" > "%NS_TMP%" 2>nul

findstr /C:":%PORT%" "%NS_TMP%" >nul 2>nul && set "FLAG=1"

del /f /q "%NS_TMP%" >nul 2>&1
endlocal & if defined FLAG set "%~2=1"
exit /b 0

:DoWarmup
if not exist "%PROJ%query_servers.py" exit /b 0
call "%PROJ%venv\Scripts\python.exe" "%PROJ%query_servers.py"
exit /b 0

:END
echo.
if %EXITCODE%==0 (
  echo === SUCCESS ===
  echo Output: "%OUT%"
  echo If the browser did not open, visit: http://127.0.0.1:5000  or  http://127.0.0.1:5001
) else (
  echo === FAILED ===
  echo See messages above.
)
echo(
echo Press any key to close...
pause >nul
endlocal
