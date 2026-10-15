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
rem Step 7: build the three images in Cloud Build (nothing is built on your PC) and push them to Artifact Registry.
rem   api (one image for every backend role)   web (Next.js, API URL baked in)   auth (Keycloak, pre-built)
setlocal
call "%~dp0_lib.cmd" init
if errorlevel 1 exit /b 1
if not defined TAG (
  %TOOLS% tag > "%WORK%\tag.txt"
  set /p TAG=<"%WORK%\tag.txt"
)
echo. & echo == Cloud Build: tag %TAG%
call gcloud builds submit "%REPO%" "--config=%HERE%\cloudbuild.yaml" --region=%REGION% "--substitutions=_REGION=%REGION%,_NAME=%NAME%,_TAG=%TAG%,_API_URL=%API_URL%,_PLANT_TZ=%TZ_PLANT%,_DEPLOY=false"
if errorlevel 1 goto :fail
>"%HERE%\.last_tag" echo %TAG%
echo   images: %AR%/api, web, auth  tag %TAG%
echo. & echo Step 7 done.
exit /b 0

:fail
echo. & echo FAILED - see the Cloud Build log above.
exit /b 1
