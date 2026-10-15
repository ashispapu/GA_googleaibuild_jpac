@echo off
rem Step 3: generated secrets, straight into Secret Manager (the temporary file is deleted at once).
rem Running it again keeps existing values. Each secret is readable only by the services that need it.
setlocal
call "%~dp0_lib.cmd" init
if errorlevel 1 exit /b 1

echo. & echo == Generated secrets
for %%s in (jwt-secret db-password kc-db-password qdrant-admin-key auth-secret kc-client-secret kc-admin-password) do (
  call "%~dp0_lib.cmd" :has_secret %%s
  if errorlevel 1 (
    %TOOLS% random 48 "%WORK%\value.txt"
    if errorlevel 1 goto :fail
    call "%~dp0_lib.cmd" :secret %%s "%WORK%\value.txt"
    if errorlevel 1 goto :fail
    del /q "%WORK%\value.txt"
    echo   %%s: generated
  ) else (
    echo   %%s: kept
  )
)
rem filled by later steps
for %%s in (qdrant-app-token redis-auth redis-ca qdrant-ca qdrant-tls-cert qdrant-tls-key keycloak-realm) do (
  call "%~dp0_lib.cmd" :secret %%s
  if errorlevel 1 goto :fail
)

echo. & echo == Who may read what
set "L=call "%~dp0_lib.cmd" :grant"
%L% jwt-secret "%SA_API% %SA_ENGINE% %SA_WORKER% %SA_OPS%"
if errorlevel 1 goto :fail
%L% db-password "%SA_API% %SA_ENGINE% %SA_WORKER% %SA_OPS%"
if errorlevel 1 goto :fail
%L% redis-auth "%SA_API% %SA_ENGINE% %SA_WORKER% %SA_OPS%"
if errorlevel 1 goto :fail
%L% redis-ca "%SA_API% %SA_ENGINE% %SA_WORKER% %SA_OPS%"
if errorlevel 1 goto :fail
%L% qdrant-ca "%SA_API% %SA_ENGINE% %SA_WORKER% %SA_OPS% %SA_QDRANT%"
if errorlevel 1 goto :fail
%L% qdrant-app-token "%SA_API% %SA_ENGINE% %SA_WORKER%"
if errorlevel 1 goto :fail
%L% qdrant-admin-key "%SA_OPS% %SA_QDRANT%"
if errorlevel 1 goto :fail
%L% qdrant-tls-cert "%SA_QDRANT%"
if errorlevel 1 goto :fail
%L% qdrant-tls-key "%SA_QDRANT%"
if errorlevel 1 goto :fail
%L% auth-secret "%SA_WEB%"
if errorlevel 1 goto :fail
%L% kc-client-secret "%SA_WEB%"
if errorlevel 1 goto :fail
%L% kc-db-password "%SA_AUTH%"
if errorlevel 1 goto :fail
%L% kc-admin-password "%SA_AUTH%"
if errorlevel 1 goto :fail
%L% keycloak-realm "%SA_AUTH%"
if errorlevel 1 goto :fail
echo. & echo Step 3 done.
exit /b 0

:fail
if exist "%WORK%\value.txt" del /q "%WORK%\value.txt"
echo. & echo FAILED - fix the error above and run %~nx0 again (it keeps what already exists).
exit /b 1
