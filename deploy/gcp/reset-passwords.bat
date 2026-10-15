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
rem Set working passwords on the Keycloak users and PROVE each one can sign in.
rem   reset-passwords.bat                  every user gets a new random password  -> deploy\gcp\user-passwords.csv
rem   reset-passwords.bat lead             only "lead" (random password)
rem   reset-passwords.bat lead MyPass123!  only "lead", with this password (12+ characters)
rem Also clears "update password" requests, enables the user and lifts brute-force lockouts.
setlocal
call "%~dp0_lib.cmd" init
if errorlevel 1 exit /b 1
echo. & echo == Keycloak %KC_URL% (realm plantguard)
call "%~dp0_lib.cmd" :read_secret kc-admin-password "%WORK%\kcadmin.txt"
if errorlevel 1 goto :fail
%TOOLS% kc-passwords "%WORK%\kcadmin.txt" "%HERE%\user-passwords.csv" %1 %2
set "RC=%errorlevel%"
del /q "%WORK%\kcadmin.txt"
if not "%RC%"=="0" goto :fail
echo. & echo Sign in at %WEB_URL% with a username and password from deploy\gcp\user-passwords.csv
exit /b 0

:fail
if exist "%WORK%\kcadmin.txt" del /q "%WORK%\kcadmin.txt"
echo. & echo FAILED - see the lines marked FAIL above (they show Keycloak's own reason).
exit /b 1
