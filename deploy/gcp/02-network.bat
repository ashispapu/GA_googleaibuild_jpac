@echo off
rem Step 2: private network. Only the public Cloud Run services have public addresses.
rem   subnet for Cloud Run direct VPC egress, subnet for the Qdrant VM, private services access (Cloud SQL + Redis),
rem   firewall (Qdrant only from Cloud Run, SSH only through IAP), Cloud NAT for the VM, private DNS zone <name>.internal
setlocal
call "%~dp0_lib.cmd" init
if errorlevel 1 exit /b 1

echo. & echo == VPC %VPC%
call gcloud compute networks describe %VPC% >nul 2>&1
if errorlevel 1 call gcloud compute networks create %VPC% --subnet-mode=custom --bgp-routing-mode=regional
if errorlevel 1 goto :fail
call gcloud compute networks subnets describe %RUN_SUBNET% --region=%REGION% >nul 2>&1
if errorlevel 1 call gcloud compute networks subnets create %RUN_SUBNET% --network=%VPC% --region=%REGION% --range=%RUN_SUBNET_RANGE% --enable-private-ip-google-access --enable-flow-logs
if errorlevel 1 goto :fail
call gcloud compute networks subnets describe %SVC_SUBNET% --region=%REGION% >nul 2>&1
if errorlevel 1 call gcloud compute networks subnets create %SVC_SUBNET% --network=%VPC% --region=%REGION% --range=%SVC_SUBNET_RANGE% --enable-private-ip-google-access --enable-flow-logs
if errorlevel 1 goto :fail

echo. & echo == Private services access (Cloud SQL, Memorystore)
call gcloud compute addresses describe %NAME%-psa --global >nul 2>&1
if errorlevel 1 call gcloud compute addresses create %NAME%-psa --global --purpose=VPC_PEERING --prefix-length=%PSA_RANGE_PREFIX% --network=%VPC%
if errorlevel 1 goto :fail
call gcloud services vpc-peerings connect --service=servicenetworking.googleapis.com --ranges=%NAME%-psa --network=%VPC% 2>nul
if errorlevel 1 call gcloud services vpc-peerings update --service=servicenetworking.googleapis.com --ranges=%NAME%-psa --network=%VPC% --force
if errorlevel 1 goto :fail

echo. & echo == Firewall
call gcloud compute firewall-rules describe %NAME%-qdrant-from-run >nul 2>&1
if errorlevel 1 call gcloud compute firewall-rules create %NAME%-qdrant-from-run --network=%VPC% --direction=INGRESS --action=ALLOW --rules=tcp:6333 "--source-ranges=%RUN_SUBNET_RANGE%,%SVC_SUBNET_RANGE%" --target-tags=qdrant "--description=Qdrant REST from Cloud Run egress only"
if errorlevel 1 goto :fail
call gcloud compute firewall-rules describe %NAME%-iap-ssh >nul 2>&1
if errorlevel 1 call gcloud compute firewall-rules create %NAME%-iap-ssh --network=%VPC% --direction=INGRESS --action=ALLOW --rules=tcp:22 --source-ranges=35.235.240.0/20 --target-tags=qdrant "--description=SSH through Identity-Aware Proxy only"
if errorlevel 1 goto :fail

echo. & echo == Cloud NAT
call gcloud compute routers describe %NAME%-router --region=%REGION% >nul 2>&1
if errorlevel 1 call gcloud compute routers create %NAME%-router --network=%VPC% --region=%REGION%
if errorlevel 1 goto :fail
call gcloud compute routers nats describe %NAME%-nat --router=%NAME%-router --region=%REGION% >nul 2>&1
if errorlevel 1 call gcloud compute routers nats create %NAME%-nat --router=%NAME%-router --region=%REGION% --nat-custom-subnet-ip-ranges=%SVC_SUBNET% --auto-allocate-nat-external-ips
if errorlevel 1 goto :fail

echo. & echo == Private DNS %NAME%.internal
call gcloud dns managed-zones describe %NAME%-internal >nul 2>&1
if errorlevel 1 call gcloud dns managed-zones create %NAME%-internal --dns-name=%NAME%.internal. --visibility=private --networks=%VPC% "--description=PlantGuard private names"
if errorlevel 1 goto :fail
echo. & echo Step 2 done.
exit /b 0

:fail
echo. & echo FAILED - fix the error above and run %~nx0 again (it skips what already exists).
exit /b 1
