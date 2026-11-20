@echo off
REM ---------------------------------------------------------------------------
REM Certificate renewal TEMPLATE for Music MP3 API (example values only).
REM
REM Copy this file to renew_mp3_cert.cmd and fill in the four variables below.
REM renew_mp3_cert.cmd is gitignored because it contains machine-specific paths.
REM
REM Requires: lego (https://go-acme.github.io/lego/) and a webroot served by
REM nginx on port 80 for HTTP-01. Run daily from Task Scheduler; lego only
REM re-issues when the certificate is close to expiry.
REM
REM Windows note: if nginx runs as a system service (NT AUTHORITY\SYSTEM), a
REM plain "nginx -s reload" may be denied. Restart the service instead.
REM ---------------------------------------------------------------------------
setlocal

REM >>> EDIT THESE FOUR VALUES <<<
set "XZ=C:\path\to\your\workspace"
set "CERT_DOMAIN=mp3.example.com"
set "CERT_EMAIL=admin@example.com"
set "NGINX_SERVICE=YourNginxService"

set "LEGO=%XZ%\.runtime\bin\lego\lego.exe"
set "DATA=%XZ%\.runtime\lego-data"
set "WEBROOT=%XZ%\path\to\webroot"
set "LOG=%~dp0cert_renew.log"

"%LEGO%" run --path "%DATA%" --accept-tos --email "%CERT_EMAIL%" ^
  --domains "%CERT_DOMAIN%" --http --http.webroot "%WEBROOT%" >> "%LOG%" 2>&1

powershell -NoProfile -Command "Restart-Service %NGINX_SERVICE% -Force" >> "%LOG%" 2>&1
endlocal
