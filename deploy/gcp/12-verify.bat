@echo off
rem Keep the window open at the end (double-click friendly): run the step as a sub-call, then wait for a key.
if "%PG_STEP_RUNNING%"=="1" goto :pg_main
set PG_STEP_RUNNING=1
call "%~f0" %*
set "PG_RC=%errorlevel%"
set PG_STEP_RUNNING=
echo.
if "%PG_RC%"=="0" (echo Finished OK.) else (echo Finished with errors - read the messages above.)
pause
exit /b %PG_RC%
:pg_main
rem Step 12: smoke test of the deployed system (public endpoints, security headers, internal services not public).
setlocal
call "%~dp0_lib.cmd" init
if errorlevel 1 exit /b 1
echo. & echo == Endpoints
%TOOLS% verify
set "RESULT=%errorlevel%"
echo. & echo == Last migrate run
call gcloud run jobs executions list --job=%NAME%-migrate --region=%REGION% --limit=1
if not "%RESULT%"=="0" (echo. & echo Some checks FAILED - see above. & exit /b 1)
echo. & echo All checks passed. Open %WEB_URL% and sign in.
exit /b 0
