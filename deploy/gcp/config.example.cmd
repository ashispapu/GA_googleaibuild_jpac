@echo off
rem PlantGuard on Google Cloud (Windows): copy to deploy\gcp\config.cmd and edit. Every step .bat reads it.
rem No spaces around "=". Keep values free of the characters  %  ^  &  |  <  >

set PROJECT_ID=pessimist12-1205
rem Cloud Run, Cloud SQL, Memorystore, the Qdrant VM and Artifact Registry live here
set REGION=asia-south1
set ZONE=asia-south1-a
rem prefix of every resource name
set NAME=plantguard
rem prod or staging (also the Qdrant collection prefix: plantguard_prod)
set ENV=prod
set ALERT_EMAIL=ops@example.com
rem one isolated Qdrant collection of repair history per plant
set PLANT_IDS=PLANT-A,PLANT-B
rem time zone shown in the web app (IANA name, e.g. Asia/Kolkata)
set TZ_PLANT=UTC

rem ---- sizes (production defaults; see the guide for the small/cheap variant) ----
set SQL_TIER=db-custom-2-7680
rem REGIONAL = automatic fail-over, ZONAL = cheaper
set SQL_HA=REGIONAL
set SQL_STORAGE_GB=50
rem standard = replica + automatic fail-over, basic = cheaper
set REDIS_TIER=standard
set REDIS_SIZE_GB=1
set QDRANT_MACHINE=e2-standard-2
set QDRANT_DISK_GB=50
set QDRANT_IMAGE=qdrant/qdrant:v1.13.4
set API_MIN=1
set API_MAX=10
set WORKER_MIN=1
set WORKER_MAX=5
set WORKER_CONCURRENCY=4
set VERTEX_LOCATION=global

rem ---- network ranges (change only if they clash with your other networks) ----
set RUN_SUBNET_RANGE=10.20.0.0/24
set SVC_SUBNET_RANGE=10.20.4.0/24
set PSA_RANGE_PREFIX=16

rem ---- tools ----
rem Python 3.10+ (the one you run PlantGuard with). Use "py" if "python" is not on PATH.
set PY=python
rem image tag: empty = 07-build.bat uses the git commit (or a timestamp)
set TAG=
