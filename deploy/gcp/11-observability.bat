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
rem Step 11: notification channel, uptime checks, error log metric and alert policies.
rem Needs the gcloud beta component for notification channels:  gcloud components install beta
setlocal
call "%~dp0_lib.cmd" init
if errorlevel 1 exit /b 1

echo. & echo == Notification channel %ALERT_EMAIL%
set "CHANNEL="
call gcloud beta monitoring channels list "--filter=labels.email_address=%ALERT_EMAIL%" "--format=value(name)" > "%WORK%\channel.txt"
if errorlevel 1 goto :fail
set /p CHANNEL=<"%WORK%\channel.txt"
if not defined CHANNEL (
  call gcloud beta monitoring channels create --type=email "--display-name=PlantGuard on-call" "--channel-labels=email_address=%ALERT_EMAIL%" "--format=value(name)" > "%WORK%\channel.txt"
  if errorlevel 1 goto :fail
)
set /p CHANNEL=<"%WORK%\channel.txt"
echo   %CHANNEL%

echo. & echo == Uptime checks
set "API_HOST=%API_URL:https://=%"
set "WEB_HOST=%WEB_URL:https://=%"
call gcloud monitoring uptime list-configs "--format=value(displayName)" > "%WORK%\uptime.txt" 2>nul
findstr /x /c:"%NAME%-api" "%WORK%\uptime.txt" >nul
if errorlevel 1 call gcloud monitoring uptime create %NAME%-api --resource-type=uptime-url "--resource-labels=host=%API_HOST%,project_id=%PROJECT_ID%" --path=/healthz --protocol=https --period=1 --timeout=10
if errorlevel 1 goto :fail
findstr /x /c:"%NAME%-web" "%WORK%\uptime.txt" >nul
if errorlevel 1 call gcloud monitoring uptime create %NAME%-web --resource-type=uptime-url "--resource-labels=host=%WEB_HOST%,project_id=%PROJECT_ID%" --path=/login --protocol=https --period=1 --timeout=10
if errorlevel 1 goto :fail

echo. & echo == Error log metric + alert policies
%TOOLS% alerts %CHANNEL%
if errorlevel 1 goto :fail
echo. & echo Step 11 done.
exit /b 0

:fail
echo. & echo FAILED - fix the error above and run %~nx0 again.
exit /b 1
