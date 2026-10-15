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
rem Step 4: Cloud SQL for PostgreSQL 16 - private IP only, TLS required, HA, daily backups + point-in-time recovery.
rem Databases: plantguard_ot, plantguard_it, keycloak. Users: plantguard (OT + IT), keycloak.
setlocal
call "%~dp0_lib.cmd" init
if errorlevel 1 exit /b 1

echo. & echo == Cloud SQL instance %SQL_INSTANCE% (about 10 minutes the first time)
call gcloud sql instances describe %SQL_INSTANCE% >nul 2>&1
if not errorlevel 1 goto :databases
call gcloud sql instances create %SQL_INSTANCE% --database-version=POSTGRES_16 --edition=ENTERPRISE --tier=%SQL_TIER% --region=%REGION% ^
  --availability-type=%SQL_HA% --storage-type=SSD --storage-size=%SQL_STORAGE_GB% --storage-auto-increase ^
  --network=projects/%PROJECT_ID%/global/networks/%VPC% --no-assign-ip --enable-google-private-path ^
  --ssl-mode=ENCRYPTED_ONLY --backup-start-time=20:00 --enable-point-in-time-recovery --retained-backups-count=14 ^
  --retained-transaction-log-days=7 --maintenance-window-day=SUN --maintenance-window-hour=21 ^
  --maintenance-release-channel=production --insights-config-query-insights-enabled --insights-config-record-application-tags ^
  "--database-flags=max_connections=500,log_min_duration_statement=500,log_lock_waits=on,cloudsql.iam_authentication=on" ^
  --password-policy-min-length=16 --enable-password-policy --deletion-protection
if errorlevel 1 goto :fail

:databases
echo. & echo == Databases and users
for %%d in (plantguard_ot plantguard_it keycloak) do (
  call gcloud sql databases describe %%d --instance=%SQL_INSTANCE% >nul 2>&1
  if errorlevel 1 call gcloud sql databases create %%d --instance=%SQL_INSTANCE%
  if errorlevel 1 goto :fail
)
call :user plantguard db-password
if errorlevel 1 goto :fail
call :user keycloak kc-db-password
if errorlevel 1 goto :fail

call "%~dp0_lib.cmd" :hosts >nul 2>&1
echo   private IP: %SQL_IP%
echo.
echo Optional least-privilege grants (run once): set a password for the "postgres" admin with
echo   gcloud sql users set-password postgres --instance=%SQL_INSTANCE% --prompt-for-password
echo then from a machine inside the VPC (or Cloud Shell with a temporary public IP) run deploy\gcp\sql\grants.sql:
echo   psql "host=%SQL_IP% user=postgres sslmode=require" -f deploy/gcp/sql/grants.sql
echo. & echo Step 4 done.
exit /b 0

:user
rem :user NAME SECRET   create the user, or reset its password to the secret's value
call "%~dp0_lib.cmd" :read_secret %2 "%WORK%\pw.txt"
if errorlevel 1 exit /b 1
set "PW="
set /p PW=<"%WORK%\pw.txt"
del /q "%WORK%\pw.txt"
call gcloud sql users describe %1 --instance=%SQL_INSTANCE% >nul 2>&1
if errorlevel 1 (
  call gcloud sql users create %1 --instance=%SQL_INSTANCE% --password=%PW% >nul
) else (
  call gcloud sql users set-password %1 --instance=%SQL_INSTANCE% --password=%PW% >nul
)
if errorlevel 1 exit /b 1
set "PW="
echo   user %1 ok
exit /b 0

:fail
echo. & echo FAILED - fix the error above and run %~nx0 again (it skips what already exists).
exit /b 1
