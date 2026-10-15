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
rem Step 6: the vector service - Qdrant on a private VM with an SSD data disk, TLS, API key + JWT RBAC.
rem   collections: <name>_<env>_manuals (shared, read-only for the app), <name>_<env>_workorders_<plant> (one per plant)
rem   persistence: daily disk snapshots (14 days) + collection snapshots copied to Cloud Storage hourly
rem The VM itself runs Linux; its boot script is deploy\qdrant\startup.sh (uploaded with Linux line endings).
setlocal
call "%~dp0_lib.cmd" init
if errorlevel 1 exit /b 1

echo. & echo == Backup bucket gs://%BACKUP_BUCKET%
call gcloud storage buckets describe gs://%BACKUP_BUCKET% >nul 2>&1
if errorlevel 1 (
  call gcloud storage buckets create gs://%BACKUP_BUCKET% --location=%REGION% --uniform-bucket-level-access --public-access-prevention --default-storage-class=STANDARD
  if errorlevel 1 goto :fail
  call gcloud storage buckets update gs://%BACKUP_BUCKET% --versioning
  if errorlevel 1 goto :fail
  %TOOLS% lifecycle "%WORK%\lifecycle.json"
  if errorlevel 1 goto :fail
  call gcloud storage buckets update gs://%BACKUP_BUCKET% "--lifecycle-file=%WORK%\lifecycle.json"
  if errorlevel 1 goto :fail
)
call gcloud storage buckets add-iam-policy-binding gs://%BACKUP_BUCKET% --member=serviceAccount:%SA_QDRANT% --role=roles/storage.objectAdmin >nul
if errorlevel 1 goto :fail

echo. & echo == TLS certificate for %QDRANT_HOST% (private CA)
call "%~dp0_lib.cmd" :has_secret qdrant-ca
if errorlevel 1 (
  %TOOLS% certs "%WORK%\certs" %QDRANT_HOST%
  if errorlevel 1 goto :fail
  call "%~dp0_lib.cmd" :secret qdrant-ca "%WORK%\certs\ca.pem"
  if errorlevel 1 goto :fail
  call "%~dp0_lib.cmd" :secret qdrant-tls-cert "%WORK%\certs\server.pem"
  if errorlevel 1 goto :fail
  call "%~dp0_lib.cmd" :secret qdrant-tls-key "%WORK%\certs\server.key"
  if errorlevel 1 goto :fail
  rmdir /s /q "%WORK%\certs"
) else (
  echo   kept the existing certificate
)

echo. & echo == Data disk %QDRANT_DISK% + daily snapshot schedule
call gcloud compute resource-policies describe %NAME%-daily --region=%REGION% >nul 2>&1
if errorlevel 1 call gcloud compute resource-policies create snapshot-schedule %NAME%-daily --region=%REGION% --daily-schedule --start-time=19:00 --max-retention-days=14 --on-source-disk-delete=keep-auto-snapshots --storage-location=%REGION%
if errorlevel 1 goto :fail
call gcloud compute disks describe %QDRANT_DISK% --zone=%ZONE% >nul 2>&1
if errorlevel 1 call gcloud compute disks create %QDRANT_DISK% --zone=%ZONE% --type=pd-ssd --size=%QDRANT_DISK_GB%GB --resource-policies=%NAME%-daily "--labels=app=%NAME%,env=%ENV%"
if errorlevel 1 goto :fail

echo. & echo == VM %QDRANT_VM% (no public IP)
%TOOLS% lf "%REPO%\deploy\qdrant\startup.sh" "%WORK%\startup.sh"
if errorlevel 1 goto :fail
call gcloud compute instances describe %QDRANT_VM% --zone=%ZONE% >nul 2>&1
if errorlevel 1 (
  call gcloud compute instances create %QDRANT_VM% --zone=%ZONE% --machine-type=%QDRANT_MACHINE% ^
    --subnet=%SVC_SUBNET% --no-address --tags=qdrant ^
    --image-family=debian-12 --image-project=debian-cloud --boot-disk-size=20GB --boot-disk-type=pd-balanced ^
    "--disk=name=%QDRANT_DISK%,device-name=qdrant-data,mode=rw,boot=no,auto-delete=no" ^
    --service-account=%SA_QDRANT% --scopes=cloud-platform ^
    --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring ^
    "--metadata=secret-prefix=%NAME%,backup-bucket=%BACKUP_BUCKET%,qdrant-image=%QDRANT_IMAGE%,enable-oslogin=TRUE" ^
    "--metadata-from-file=startup-script=%WORK%\startup.sh" ^
    "--labels=app=%NAME%,env=%ENV%,role=qdrant"
    if errorlevel 1 goto :fail
) else (
  call gcloud compute instances add-metadata %QDRANT_VM% --zone=%ZONE% "--metadata=qdrant-image=%QDRANT_IMAGE%" "--metadata-from-file=startup-script=%WORK%\startup.sh"
  if errorlevel 1 goto :fail
)
set "QDRANT_IP="
call gcloud compute instances describe %QDRANT_VM% --zone=%ZONE% "--format=value(networkInterfaces[0].networkIP)" > "%WORK%\qdrant-ip.txt"
set /p QDRANT_IP=<"%WORK%\qdrant-ip.txt"
if not defined QDRANT_IP goto :fail

echo. & echo == DNS %QDRANT_HOST% to %QDRANT_IP%
call gcloud dns record-sets describe %QDRANT_HOST%. --type=A --zone=%NAME%-internal >nul 2>&1
if errorlevel 1 (
  call gcloud dns record-sets create %QDRANT_HOST%. --type=A --ttl=60 --rrdatas=%QDRANT_IP% --zone=%NAME%-internal
  if errorlevel 1 goto :fail
) else (
  call gcloud dns record-sets update %QDRANT_HOST%. --type=A --ttl=60 --rrdatas=%QDRANT_IP% --zone=%NAME%-internal
  if errorlevel 1 goto :fail
)
echo.
echo Qdrant: https://%QDRANT_HOST%:6333 inside the VPC. It needs about 2 minutes to boot. Check with:
echo   gcloud compute instances get-serial-port-output %QDRANT_VM% --zone=%ZONE% ^| findstr /c:"qdrant started"
echo. & echo Step 6 done.
exit /b 0

:fail
if exist "%WORK%\certs" rmdir /s /q "%WORK%\certs"
echo. & echo FAILED - fix the error above and run %~nx0 again (it skips what already exists).
exit /b 1
