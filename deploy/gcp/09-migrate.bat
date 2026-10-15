@echo off
rem Step 9: the migrate job (Cloud Run job): creates/upgrades both schemas, seeds a new plant, creates the Qdrant
rem collections and fills them. Idempotent - run it before every release. Uses the Qdrant admin key.
rem Then mints the app's Qdrant token (read manuals, read/write each plant's repairs, nothing else).
setlocal
call "%~dp0_lib.cmd" init
if errorlevel 1 exit /b 1
if not defined TAG (
  if not exist "%HERE%\.last_tag" (echo Run 07-build.bat first. & exit /b 1)
  set /p TAG=<"%HERE%\.last_tag"
)
call "%~dp0_lib.cmd" :hosts
if errorlevel 1 goto :fail

echo. & echo == Job %NAME%-migrate
%TOOLS% backend-env "%WORK%\migrate-env.yaml" migrate
if errorlevel 1 goto :fail
call gcloud run jobs deploy %NAME%-migrate --image=%AR%/api:%TAG% --region=%REGION% ^
  --service-account=%SA_OPS% --command=python --args=-m,app.ops,migrate ^
  --tasks=1 --max-retries=1 --task-timeout=1800 --cpu=2 --memory=2Gi %NET% ^
  "--env-vars-file=%WORK%\migrate-env.yaml" "--set-secrets=%SECRETS_ADMIN%" "--labels=app=%NAME%,env=%ENV%,role=migrate"
if errorlevel 1 goto :fail
call gcloud run jobs execute %NAME%-migrate --region=%REGION% --wait
if errorlevel 1 goto :fail

echo. & echo == Qdrant token for the app
call "%~dp0_lib.cmd" :has_secret qdrant-app-token
if errorlevel 1 goto :mint
if "%ROTATE_QDRANT_TOKEN%"=="1" goto :mint
echo   kept (set ROTATE_QDRANT_TOKEN=1 and run again to rotate, then run 10-services.bat)
goto :done

:mint
call "%~dp0_lib.cmd" :read_secret qdrant-admin-key "%WORK%\qk.txt"
if errorlevel 1 goto :fail
%TOOLS% token app %VECTOR_PREFIX% %PLANT_IDS% 365 "%WORK%\qk.txt" "%WORK%\qt.txt"
if errorlevel 1 goto :fail
del /q "%WORK%\qk.txt"
call "%~dp0_lib.cmd" :secret qdrant-app-token "%WORK%\qt.txt"
if errorlevel 1 goto :fail
del /q "%WORK%\qt.txt"
echo   minted (valid 365 days)

:done
echo. & echo Step 9 done.
exit /b 0

:fail
for %%f in (qk.txt qt.txt) do if exist "%WORK%\%%f" del /q "%WORK%\%%f"
echo. & echo FAILED - fix the error above and run %~nx0 again. Job logs:
echo   gcloud run jobs executions list --job=%NAME%-migrate --region=%REGION%
exit /b 1
