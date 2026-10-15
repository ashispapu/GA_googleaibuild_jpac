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
rem Release a new version: build the images, run the migrate job, then roll out engine, worker, api and web
rem (each waits for its startup probe; a revision that never gets ready never gets traffic).
rem Same pipeline as a Cloud Build trigger on your main branch (deploy\gcp\cloudbuild.yaml with _DEPLOY=true).
setlocal
call "%~dp0_lib.cmd" init
if errorlevel 1 exit /b 1
if not defined TAG (
  %TOOLS% tag > "%WORK%\tag.txt"
  set /p TAG=<"%WORK%\tag.txt"
)
echo. & echo == Release %TAG%
call gcloud builds submit "%REPO%" "--config=%HERE%\cloudbuild.yaml" --region=%REGION% "--substitutions=_REGION=%REGION%,_NAME=%NAME%,_TAG=%TAG%,_API_URL=%API_URL%,_PLANT_TZ=%TZ_PLANT%,_DEPLOY=true"
if errorlevel 1 (echo. & echo FAILED - see the Cloud Build log above. Nothing after the failing step was rolled out. & exit /b 1)
>"%HERE%\.last_tag" echo %TAG%
echo. & echo Released %TAG%. Roll back:  gcloud run services update-traffic %NAME%-api --to-revisions=PREVIOUS_REVISION=100
exit /b 0
