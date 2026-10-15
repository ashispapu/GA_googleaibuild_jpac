@echo off
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
