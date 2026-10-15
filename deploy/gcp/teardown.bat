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
rem Deletes what steps 1-12 created (NOT reversible). Lift Cloud SQL deletion protection first:
rem   gcloud sql instances patch <name>-pg --no-deletion-protection
rem Kept on purpose (delete by hand when sure): the Qdrant data disk and its snapshots, the backup bucket, secrets.
setlocal
call "%~dp0_lib.cmd" init
if errorlevel 1 exit /b 1
set "ANSWER="
set /p ANSWER=Delete ALL PlantGuard resources in %PROJECT_ID%? Type the project id to confirm: 
if not "%ANSWER%"=="%PROJECT_ID%" (echo Cancelled. & exit /b 1)
for %%s in (web api worker engine auth) do call gcloud run services delete %NAME%-%%s --region=%REGION% -q
for %%j in (migrate backup) do call gcloud run jobs delete %NAME%-%%j --region=%REGION% -q
call gcloud scheduler jobs delete %NAME%-backup --location=%REGION% -q
call gcloud compute instances delete %QDRANT_VM% --zone=%ZONE% -q
call gcloud redis instances delete %REDIS_INSTANCE% --region=%REGION% -q
call gcloud sql instances delete %SQL_INSTANCE% -q
echo Done. Still there: disk %QDRANT_DISK% and its snapshots, bucket gs://%BACKUP_BUCKET%, secrets, network.
exit /b 0
