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
rem Step 8: Keycloak (sign-in) on Cloud Run, backed by the "keycloak" database.
rem   The production realm is generated from config\keycloak\plantguard-realm.json (web URLs, new client secret,
rem   password policy, no password-grant client) and mounted from Secret Manager.
rem   Demo users are kept with one-time passwords (written to deploy\gcp\initial-passwords.csv) unless DROP_USERS=1.
setlocal
call "%~dp0_lib.cmd" init
if errorlevel 1 exit /b 1
call :tag
if errorlevel 1 goto :fail
call "%~dp0_lib.cmd" :hosts
if errorlevel 1 goto :fail

call "%~dp0_lib.cmd" :has_secret keycloak-realm
if errorlevel 1 goto :realm
if "%REGENERATE_REALM%"=="1" goto :realm
goto :deploy

:realm
echo. & echo == Production realm
call "%~dp0_lib.cmd" :read_secret kc-client-secret "%WORK%\kc-client.txt"
if errorlevel 1 goto :fail
set "USERS=--temp-passwords "%HERE%\initial-passwords.csv""
if "%DROP_USERS%"=="1" set "USERS=--drop-users"
call %PY% "%REPO%\deploy\keycloak\prepare_realm.py" --web-url %WEB_URL% --client-secret-file "%WORK%\kc-client.txt" --out "%WORK%\realm.json" %USERS%
if errorlevel 1 goto :fail
del /q "%WORK%\kc-client.txt"
call "%~dp0_lib.cmd" :secret keycloak-realm "%WORK%\realm.json"
if errorlevel 1 goto :fail
del /q "%WORK%\realm.json"
if not "%DROP_USERS%"=="1" echo   one-time passwords: deploy\gcp\initial-passwords.csv - hand them out, then delete the file

:deploy
echo. & echo == Keycloak service %NAME%-auth at %KC_URL%
%TOOLS% envfile "%WORK%\auth-env.yaml" "KC_DB_URL=jdbc:postgresql://%SQL_IP%:5432/keycloak?sslmode=require" KC_DB_USERNAME=keycloak KC_HOSTNAME=%KC_URL% KC_LOG_CONSOLE_OUTPUT=json KC_BOOTSTRAP_ADMIN_USERNAME=kcadmin
if errorlevel 1 goto :fail
call gcloud run deploy %NAME%-auth --image=%AR%/auth:%TAG% --region=%REGION% ^
  --service-account=%SA_AUTH% --allow-unauthenticated --ingress=all ^
  --cpu=2 --memory=2Gi --min-instances=1 --max-instances=1 --no-cpu-throttling --cpu-boost ^
  --concurrency=80 --timeout=300 --port=8080 %NET% ^
  "--startup-probe=tcpSocket.port=8080,initialDelaySeconds=10,periodSeconds=5,failureThreshold=36,timeoutSeconds=3" ^
  "--env-vars-file=%WORK%\auth-env.yaml" ^
  "--set-secrets=KC_DB_PASSWORD=%NAME%-kc-db-password:latest,KC_BOOTSTRAP_ADMIN_PASSWORD=%NAME%-kc-admin-password:latest,/opt/keycloak/data/import/plantguard-realm.json=%NAME%-keycloak-realm:latest" ^
  "--labels=app=%NAME%,env=%ENV%,role=auth"
if errorlevel 1 goto :fail
echo.
echo Admin console: %KC_URL%/admin  (user kcadmin; password:  gcloud secrets versions access latest --secret=%NAME%-kc-admin-password)
echo. & echo Step 8 done.
exit /b 0

:tag
if defined TAG exit /b 0
if not exist "%HERE%\.last_tag" (echo Run 07-build.bat first. & exit /b 1)
set /p TAG=<"%HERE%\.last_tag"
exit /b 0

:fail
for %%f in (kc-client.txt realm.json) do if exist "%WORK%\%%f" del /q "%WORK%\%%f"
echo. & echo FAILED - fix the error above and run %~nx0 again.
exit /b 1
