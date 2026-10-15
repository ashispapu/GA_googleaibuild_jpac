@echo off
rem Step 10: the PlantGuard services (one image, one role each) and the web app.
rem   engine  exactly one active (Redis lease), internal only, CPU always on: simulation, live scoring, alerts
rem   worker  1..N, internal only, CPU always on: takes analyses from the Redis run queue and runs the agents
rem   api     1..N, public HTTPS + WebSockets, session affinity: everything the web app calls
rem   web     1..N, public: the Next.js app (signs in through Keycloak)
setlocal
call "%~dp0_lib.cmd" init
if errorlevel 1 exit /b 1
if not defined TAG (
  if not exist "%HERE%\.last_tag" (echo Run 07-build.bat first. & exit /b 1)
  set /p TAG=<"%HERE%\.last_tag"
)
call "%~dp0_lib.cmd" :hosts
if errorlevel 1 goto :fail
set "PROBES="--startup-probe=httpGet.path=/readyz,httpGet.port=8000,initialDelaySeconds=5,periodSeconds=5,failureThreshold=30,timeoutSeconds=4" "--liveness-probe=httpGet.path=/healthz,httpGet.port=8000,periodSeconds=15,failureThreshold=4,timeoutSeconds=4""
set "COMMON=--image=%AR%/api:%TAG% --region=%REGION% --port=8000 %NET% %PROBES% "--set-secrets=%SECRETS_APP%" --execution-environment=gen2 --cpu-boost"

echo. & echo == engine
%TOOLS% backend-env "%WORK%\engine-env.yaml" engine
if errorlevel 1 goto :fail
call gcloud run deploy %NAME%-engine %COMMON% --service-account=%SA_ENGINE% "--env-vars-file=%WORK%\engine-env.yaml" ^
  --ingress=internal --no-allow-unauthenticated --min-instances=1 --max-instances=1 --no-cpu-throttling ^
  --cpu=1 --memory=1Gi --concurrency=20 "--labels=app=%NAME%,env=%ENV%,role=engine"
if errorlevel 1 goto :fail

echo. & echo == worker
%TOOLS% backend-env "%WORK%\worker-env.yaml" worker RUN_RECLAIM_IDLE_S=300
if errorlevel 1 goto :fail
call gcloud run deploy %NAME%-worker %COMMON% --service-account=%SA_WORKER% "--env-vars-file=%WORK%\worker-env.yaml" ^
  --ingress=internal --no-allow-unauthenticated --min-instances=%WORKER_MIN% --max-instances=%WORKER_MAX% ^
  --no-cpu-throttling --cpu=2 --memory=2Gi --concurrency=10 "--labels=app=%NAME%,env=%ENV%,role=worker"
if errorlevel 1 goto :fail

echo. & echo == api at %API_URL%
%TOOLS% backend-env "%WORK%\api-env.yaml" api
if errorlevel 1 goto :fail
call gcloud run deploy %NAME%-api %COMMON% --service-account=%SA_API% "--env-vars-file=%WORK%\api-env.yaml" ^
  --ingress=all --allow-unauthenticated --min-instances=%API_MIN% --max-instances=%API_MAX% ^
  --no-cpu-throttling --cpu=2 --memory=2Gi --concurrency=80 --timeout=3600 --session-affinity ^
  "--labels=app=%NAME%,env=%ENV%,role=api"
if errorlevel 1 goto :fail

echo. & echo == web at %WEB_URL%
%TOOLS% envfile "%WORK%\web-env.yaml" AUTH_URL=%WEB_URL% AUTH_TRUST_HOST=true AUTH_KEYCLOAK_ID=plantguard-web AUTH_KEYCLOAK_ISSUER=%KC_URL%/realms/plantguard
if errorlevel 1 goto :fail
call gcloud run deploy %NAME%-web --image=%AR%/web:%TAG% --region=%REGION% --port=3000 ^
  --service-account=%SA_WEB% --ingress=all --allow-unauthenticated --min-instances=1 --max-instances=5 ^
  --cpu=1 --memory=1Gi --concurrency=80 --cpu-boost "--env-vars-file=%WORK%\web-env.yaml" ^
  "--set-secrets=AUTH_SECRET=%NAME%-auth-secret:latest,AUTH_KEYCLOAK_SECRET=%NAME%-kc-client-secret:latest" ^
  "--labels=app=%NAME%,env=%ENV%,role=web"
if errorlevel 1 goto :fail

echo. & echo == Backup job (Qdrant collection snapshots; the VM copies them to gs://%BACKUP_BUCKET%)
%TOOLS% backend-env "%WORK%\backup-env.yaml" migrate
if errorlevel 1 goto :fail
call gcloud run jobs deploy %NAME%-backup --image=%AR%/api:%TAG% --region=%REGION% ^
  --service-account=%SA_OPS% --command=python --args=-m,app.ops,snapshot ^
  --tasks=1 --max-retries=2 --task-timeout=900 --cpu=1 --memory=1Gi %NET% ^
  "--env-vars-file=%WORK%\backup-env.yaml" "--set-secrets=%SECRETS_ADMIN%" "--labels=app=%NAME%,env=%ENV%,role=backup"
if errorlevel 1 goto :fail
call gcloud scheduler jobs describe %NAME%-backup --location=%REGION% >nul 2>&1
if errorlevel 1 call gcloud scheduler jobs create http %NAME%-backup --location=%REGION% "--schedule=30 */6 * * *" --time-zone=Etc/UTC --http-method=POST "--uri=https://run.googleapis.com/v2/projects/%PROJECT_ID%/locations/%REGION%/jobs/%NAME%-backup:run" --oauth-service-account-email=%SA_SCHED%
if errorlevel 1 goto :fail

echo.
echo web app : %WEB_URL%
echo api     : %API_URL%   (health: %API_URL%/healthz)
echo sign-in : %KC_URL%
echo. & echo Step 10 done.
exit /b 0

:fail
echo. & echo FAILED - fix the error above and run %~nx0 again. Logs of a service:
echo   gcloud run services logs read %NAME%-api --region=%REGION% --limit=50
exit /b 1
