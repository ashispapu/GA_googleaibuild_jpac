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
rem Step 5: Memorystore for Redis 7.2 - replica with automatic fail-over, AUTH, in-transit TLS, RDB snapshots.
rem Event bus, analysis run queue, single-use approval tokens, shared rate limits, plant-engine lease, caches.
setlocal
call "%~dp0_lib.cmd" init
if errorlevel 1 exit /b 1

echo. & echo == Memorystore %REDIS_INSTANCE% (about 5 minutes the first time)
call gcloud redis instances describe %REDIS_INSTANCE% --region=%REGION% >nul 2>&1
if not errorlevel 1 goto :secrets
set "PERSIST="
if /i "%REDIS_TIER%"=="standard" set "PERSIST=--persistence-mode=rdb --rdb-snapshot-period=1h"
call gcloud redis instances create %REDIS_INSTANCE% --region=%REGION% --tier=%REDIS_TIER% --size=%REDIS_SIZE_GB% --redis-version=redis_7_2 ^
  --network=projects/%PROJECT_ID%/global/networks/%VPC% --connect-mode=private-service-access ^
  --enable-auth --transit-encryption-mode=server-authentication --redis-config=maxmemory-policy=volatile-lru ^
  --maintenance-window-day=sunday --maintenance-window-hour=21 "--labels=app=%NAME%,env=%ENV%" ^
  "--display-name=PlantGuard bus and queue" %PERSIST%
if errorlevel 1 goto :fail

:secrets
echo. & echo == AUTH string and server CA to Secret Manager
call gcloud redis instances get-auth-string %REDIS_INSTANCE% --region=%REGION% "--format=value(authString)" > "%WORK%\redis-auth.txt"
if errorlevel 1 goto :fail
%TOOLS% strip "%WORK%\redis-auth.txt"
if errorlevel 1 goto :fail
call "%~dp0_lib.cmd" :secret redis-auth "%WORK%\redis-auth.txt"
if errorlevel 1 goto :fail
del /q "%WORK%\redis-auth.txt"
call gcloud redis instances describe %REDIS_INSTANCE% --region=%REGION% "--format=value(serverCaCerts[0].cert)" > "%WORK%\redis-ca.pem"
if errorlevel 1 goto :fail
call "%~dp0_lib.cmd" :secret redis-ca "%WORK%\redis-ca.pem"
if errorlevel 1 goto :fail
del /q "%WORK%\redis-ca.pem"
call "%~dp0_lib.cmd" :hosts >nul 2>&1
echo   host: %REDIS_HOST%:6378 (TLS)
echo. & echo Step 5 done.
exit /b 0

:fail
if exist "%WORK%\redis-auth.txt" del /q "%WORK%\redis-auth.txt"
echo. & echo FAILED - fix the error above and run %~nx0 again (it skips what already exists).
exit /b 1
