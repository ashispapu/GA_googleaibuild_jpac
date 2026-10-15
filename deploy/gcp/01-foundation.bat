@echo off
rem Step 1: APIs, Artifact Registry, one service account per component and its project roles.
setlocal
call "%~dp0_lib.cmd" init
if errorlevel 1 exit /b 1

echo. & echo == Enabling APIs (takes a minute)
call gcloud services enable run.googleapis.com compute.googleapis.com sqladmin.googleapis.com redis.googleapis.com servicenetworking.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com aiplatform.googleapis.com dns.googleapis.com cloudscheduler.googleapis.com monitoring.googleapis.com logging.googleapis.com cloudtrace.googleapis.com iap.googleapis.com
if errorlevel 1 goto :fail

echo. & echo == Artifact Registry %AR%
call gcloud artifacts repositories describe %NAME% --location=%REGION% >nul 2>&1
if errorlevel 1 call gcloud artifacts repositories create %NAME% --repository-format=docker --location=%REGION% "--description=PlantGuard images"
if errorlevel 1 goto :fail

echo. & echo == Service accounts
for %%s in (api engine worker ops web auth qdrant scheduler) do (
  call gcloud iam service-accounts describe %NAME%-%%s@%PROJECT_ID%.iam.gserviceaccount.com >nul 2>&1
  if errorlevel 1 call gcloud iam service-accounts create %NAME%-%%s "--display-name=PlantGuard %%s"
  if errorlevel 1 goto :fail
)

echo. & echo == Project roles
for %%a in (%SA_API% %SA_ENGINE% %SA_WORKER% %SA_OPS% %SA_WEB% %SA_AUTH% %SA_QDRANT%) do (
  call :bind %%a roles/logging.logWriter
  if errorlevel 1 goto :fail
  call :bind %%a roles/monitoring.metricWriter
  if errorlevel 1 goto :fail
)
rem Gemini on Vertex AI (agents, options chat, assistant) + traces
for %%a in (%SA_API% %SA_ENGINE% %SA_WORKER%) do (
  call :bind %%a roles/cloudtrace.agent
  if errorlevel 1 goto :fail
  call :bind %%a roles/aiplatform.user
  if errorlevel 1 goto :fail
)
call :bind %SA_OPS% roles/aiplatform.user
if errorlevel 1 goto :fail
call :bind %SA_SCHED% roles/run.invoker
if errorlevel 1 goto :fail

rem Cloud Build deploys with the default compute service account
set "CB_SA=%PROJECT_NUMBER%-compute@developer.gserviceaccount.com"
call :bind %CB_SA% roles/run.admin
if errorlevel 1 goto :fail
call :bind %CB_SA% roles/artifactregistry.writer
if errorlevel 1 goto :fail
for %%a in (%SA_API% %SA_ENGINE% %SA_WORKER% %SA_OPS% %SA_WEB% %SA_AUTH%) do (
  call gcloud iam service-accounts add-iam-policy-binding %%a --member=serviceAccount:%CB_SA% --role=roles/iam.serviceAccountUser >nul
  if errorlevel 1 goto :fail
)
echo. & echo Step 1 done.
exit /b 0

:bind
call gcloud projects add-iam-policy-binding %PROJECT_ID% --member=serviceAccount:%~1 --role=%~2 --condition=None >nul
if errorlevel 1 exit /b 1
echo   %~2 for %~1
exit /b 0

:fail
echo. & echo FAILED - fix the error above and run %~nx0 again (it skips what already exists).
exit /b 1
