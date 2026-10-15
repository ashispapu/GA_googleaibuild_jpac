@echo off
rem Shared setup and helpers for deploy\gcp\*.bat (not run on its own).
rem   call "%~dp0_lib.cmd" init                       read config.cmd, sign-in check, every resource name
rem   call "%~dp0_lib.cmd" :secret NAME [FILE]        create secret <name>-NAME if missing; add FILE as a new version
rem   call "%~dp0_lib.cmd" :has_secret NAME           errorlevel 0 when the secret has a value
rem   call "%~dp0_lib.cmd" :read_secret NAME FILE     write the secret's value to FILE
rem   call "%~dp0_lib.cmd" :grant NAME "SA SA ..."     let these service accounts read the secret
rem   call "%~dp0_lib.cmd" :hosts                     SQL_IP and REDIS_HOST of the running instances
rem Note: gcloud is gcloud.cmd on Windows, so every call to it is "call gcloud ..." (without "call" the script stops).
if /i "%~1"=="init" goto :init
goto %~1

:init
set "HERE=%~dp0"
set "HERE=%HERE:~0,-1%"
for %%i in ("%HERE%\..\..") do set "REPO=%%~fi"
if not exist "%HERE%\config.cmd" (
  echo Copy deploy\gcp\config.example.cmd to deploy\gcp\config.cmd and edit it first.
  exit /b 1
)
call "%HERE%\config.cmd"
if not defined PY set "PY=python"
rem "call" also works when PY is a .cmd wrapper (a .cmd started without call would end this script)
set "TOOLS=call %PY% "%HERE%\tools.py""
set "WORK=%TEMP%\plantguard-deploy"
if not exist "%WORK%" mkdir "%WORK%"
where gcloud >nul 2>&1
if errorlevel 1 (
  echo gcloud was not found. Install the Google Cloud CLI: https://cloud.google.com/sdk/docs/install
  exit /b 1
)
call %PY% --version >nul 2>&1
if errorlevel 1 (
  echo Python was not found as "%PY%". Set PY in deploy\gcp\config.cmd, e.g. set PY=py
  exit /b 1
)
call gcloud config set project %PROJECT_ID% >nul 2>&1
call gcloud config set run/region %REGION% >nul 2>&1
set "PROJECT_NUMBER="
call gcloud projects describe %PROJECT_ID% "--format=value(projectNumber)" > "%WORK%\project.txt" 2>nul
set /p PROJECT_NUMBER=<"%WORK%\project.txt"
if not defined PROJECT_NUMBER (
  echo Cannot read project %PROJECT_ID%. Sign in first:  gcloud auth login
  exit /b 1
)
set "AR=%REGION%-docker.pkg.dev/%PROJECT_ID%/%NAME%"
set "VPC=%NAME%-vpc"
set "RUN_SUBNET=%NAME%-run"
set "SVC_SUBNET=%NAME%-svc"
set "SQL_INSTANCE=%NAME%-pg"
set "REDIS_INSTANCE=%NAME%-redis"
set "QDRANT_VM=%NAME%-qdrant"
set "QDRANT_DISK=%NAME%-qdrant-data"
set "QDRANT_HOST=qdrant.%NAME%.internal"
set "BACKUP_BUCKET=%PROJECT_ID%-%NAME%-backups"
set "VECTOR_PREFIX=%NAME%_%ENV%"
rem Cloud Run URLs are known before the first deploy: https://<service>-<project number>.<region>.run.app
set "API_URL=https://%NAME%-api-%PROJECT_NUMBER%.%REGION%.run.app"
set "WEB_URL=https://%NAME%-web-%PROJECT_NUMBER%.%REGION%.run.app"
set "KC_URL=https://%NAME%-auth-%PROJECT_NUMBER%.%REGION%.run.app"
set "SA_API=%NAME%-api@%PROJECT_ID%.iam.gserviceaccount.com"
set "SA_ENGINE=%NAME%-engine@%PROJECT_ID%.iam.gserviceaccount.com"
set "SA_WORKER=%NAME%-worker@%PROJECT_ID%.iam.gserviceaccount.com"
set "SA_OPS=%NAME%-ops@%PROJECT_ID%.iam.gserviceaccount.com"
set "SA_WEB=%NAME%-web@%PROJECT_ID%.iam.gserviceaccount.com"
set "SA_AUTH=%NAME%-auth@%PROJECT_ID%.iam.gserviceaccount.com"
set "SA_QDRANT=%NAME%-qdrant@%PROJECT_ID%.iam.gserviceaccount.com"
set "SA_SCHED=%NAME%-scheduler@%PROJECT_ID%.iam.gserviceaccount.com"
set "NET=--network=%VPC% --subnet=%RUN_SUBNET% --vpc-egress=private-ranges-only"
set "SECRET_FILES=/etc/plantguard/redis/ca.pem=%NAME%-redis-ca:latest,/etc/plantguard/qdrant/ca.pem=%NAME%-qdrant-ca:latest"
set "SECRETS_APP=DB_PASSWORD=%NAME%-db-password:latest,REDIS_AUTH=%NAME%-redis-auth:latest,JWT_SECRET=%NAME%-jwt-secret:latest,QDRANT_API_KEY=%NAME%-qdrant-app-token:latest,%SECRET_FILES%"
set "SECRETS_ADMIN=DB_PASSWORD=%NAME%-db-password:latest,REDIS_AUTH=%NAME%-redis-auth:latest,JWT_SECRET=%NAME%-jwt-secret:latest,QDRANT_API_KEY=%NAME%-qdrant-admin-key:latest,%SECRET_FILES%"
echo Project %PROJECT_ID% (%PROJECT_NUMBER%), region %REGION%, prefix %NAME%
exit /b 0

:secret
call gcloud secrets describe %NAME%-%~2 >nul 2>&1
if errorlevel 1 call gcloud secrets create %NAME%-%~2 --replication-policy=automatic "--labels=app=%NAME%,env=%ENV%" >nul
if errorlevel 1 exit /b 1
if "%~3"=="" exit /b 0
call gcloud secrets versions add %NAME%-%~2 "--data-file=%~3" >nul
exit /b %errorlevel%

:has_secret
call gcloud secrets versions access latest --secret=%NAME%-%~2 >nul 2>&1
exit /b %errorlevel%

:read_secret
call gcloud secrets versions access latest --secret=%NAME%-%~2 > "%~3"
exit /b %errorlevel%

:grant
rem :grant NAME "SA1 SA2 ..."   (the service accounts as one quoted, space-separated argument)
set "_SECRET=%NAME%-%~2"
for %%a in (%~3) do (
  call gcloud secrets add-iam-policy-binding %_SECRET% --member=serviceAccount:%%a --role=roles/secretmanager.secretAccessor --condition=None >nul
  if errorlevel 1 exit /b 1
)
echo   %~2: readable by the services that need it
exit /b 0

:hosts
set "SQL_IP="
set "REDIS_HOST="
call gcloud sql instances describe %SQL_INSTANCE% "--format=value(ipAddresses[0].ipAddress)" > "%WORK%\sqlip.txt"
set /p SQL_IP=<"%WORK%\sqlip.txt"
call gcloud redis instances describe %REDIS_INSTANCE% --region=%REGION% "--format=value(host)" > "%WORK%\redis.txt"
set /p REDIS_HOST=<"%WORK%\redis.txt"
if not defined SQL_IP (echo Cloud SQL instance %SQL_INSTANCE% not found - run 04-database.bat first & exit /b 1)
if not defined REDIS_HOST (echo Redis instance %REDIS_INSTANCE% not found - run 05-redis.bat first & exit /b 1)
exit /b 0
