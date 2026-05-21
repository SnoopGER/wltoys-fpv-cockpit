@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
set "OUTLOG=%ROOT%data\server.out.log"
set "ERRLOG=%ROOT%data\server.err.log"
set "LOCAL_URL=http://127.0.0.1:5555"
set "PUBLIC_URL=https://race.zen-rc.net"
set "MODE=%~1"

cd /d "%ROOT%"

if /I "%MODE%"=="status" goto status
if /I "%MODE%"=="start" goto start_dashboard
if /I "%MODE%"=="stop" goto stop_dashboard
if /I "%MODE%"=="restart" goto restart_dashboard
if /I "%MODE%"=="logs" goto logs
if /I "%MODE%"=="local" goto local_testmode
if /I "%MODE%"=="testmode" goto local_testmode
if /I "%MODE%"=="live" goto live_mode

:menu
cls
echo ============================================================
echo   WLtoys FPV Dashboard Control
echo ============================================================
echo.
echo   Repo: %ROOT%
echo.
echo   1) Status
echo   2) Start dashboard
echo   3) Stop dashboard
echo   4) Restart dashboard
echo   5) Open local dashboard
echo   6) Open public dashboard
echo   7) Show latest logs
echo   8) Restart Cloudflared service
echo   9) LOCAL TESTMODE - stop Cloudflare tunnel
echo   10) LIVE MODE - start Cloudflare tunnel
echo   11) Exit
echo.
set /p "choice=Select: "

if "%choice%"=="1" goto status
if "%choice%"=="2" goto start_dashboard
if "%choice%"=="3" goto stop_dashboard
if "%choice%"=="4" goto restart_dashboard
if "%choice%"=="5" goto open_local
if "%choice%"=="6" goto open_public
if "%choice%"=="7" goto logs
if "%choice%"=="8" goto restart_cloudflare
if "%choice%"=="9" goto local_testmode
if "%choice%"=="10" goto live_mode
if "%choice%"=="11" goto end
goto menu

:status
echo.
echo Checking dashboard and Cloudflare status...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$listener = Get-NetTCPConnection -LocalPort 5555 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; " ^
  "if ($listener) { Write-Host ('TCP listener: visible on 5555 (PID ' + ($listener -join ', ') + ')') -ForegroundColor Green } else { Write-Host 'TCP listener: not visible via Get-NetTCPConnection' -ForegroundColor Yellow }; " ^
  "try { $local = Invoke-WebRequest -UseBasicParsing '%LOCAL_URL%/api/status' -TimeoutSec 5; Write-Host ('Dashboard local API: RUNNING ' + $local.StatusCode) -ForegroundColor Green } catch { Write-Host ('Dashboard local API: STOPPED/FAIL - ' + $_.Exception.Message) -ForegroundColor Red }; " ^
  "try { $public = Invoke-WebRequest -UseBasicParsing '%PUBLIC_URL%/api/status' -TimeoutSec 8; Write-Host ('Cloudflare route: OK ' + $public.StatusCode) -ForegroundColor Green } catch { $code = if ($_.Exception.Response) { $_.Exception.Response.StatusCode.value__ } else { 'n/a' }; Write-Host ('Cloudflare route: FAIL ' + $code + ' - ' + $_.Exception.Message) -ForegroundColor Red }; " ^
  "$svc = Get-Service Cloudflared -ErrorAction SilentlyContinue; if ($svc) { if ($svc.Status -eq 'Running') { Write-Host ('Cloudflared service: ' + $svc.Status + ' (LIVE MODE)') -ForegroundColor Green } elseif ($svc.Status -eq 'Stopped') { Write-Host ('Cloudflared service: ' + $svc.Status + ' (LOCAL TESTMODE)') -ForegroundColor Yellow } else { Write-Host ('Cloudflared service: ' + $svc.Status) -ForegroundColor Yellow } } else { Write-Host 'Cloudflared service: NOT FOUND' -ForegroundColor Red }"
echo.
if defined MODE goto end
pause
goto menu

:start_dashboard
echo.
echo Starting dashboard...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; " ^
  "$root = '%ROOT%'; $python = '%PYTHON%'; " ^
  "$listener = Get-NetTCPConnection -LocalPort 5555 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; " ^
  "if ($listener) { Write-Host ('Dashboard already running on TCP 5555 (PID ' + ($listener -join ', ') + ')') -ForegroundColor Yellow; exit 0 }; " ^
  "if (!(Test-Path $python)) { Write-Host 'Creating .venv and installing requirements...'; python -m venv (Join-Path $root '.venv'); & $python -m pip install -r (Join-Path $root 'requirements.txt') }; " ^
  "New-Item -ItemType Directory -Force -Path (Join-Path $root 'data') | Out-Null; " ^
  "Remove-Item '%OUTLOG%', '%ERRLOG%' -ErrorAction SilentlyContinue; " ^
  "Start-Process -FilePath $python -ArgumentList '-u webapp.py' -WorkingDirectory $root -RedirectStandardOutput '%OUTLOG%' -RedirectStandardError '%ERRLOG%' -WindowStyle Hidden; " ^
  "Start-Sleep -Seconds 4; " ^
  "try { $r = Invoke-WebRequest -UseBasicParsing '%LOCAL_URL%/api/status' -TimeoutSec 5; Write-Host ('Dashboard started. Local API: OK ' + $r.StatusCode) -ForegroundColor Green } catch { Write-Host ('Dashboard start requested, but API did not answer: ' + $_.Exception.Message) -ForegroundColor Red }"
echo.
if defined MODE goto end
pause
goto menu

:stop_dashboard
echo.
echo Stopping dashboard...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$listeners = Get-NetTCPConnection -LocalPort 5555 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; " ^
  "if (!$listeners) { Write-Host 'Dashboard is not running.' -ForegroundColor Yellow; exit 0 }; " ^
  "foreach ($procId in $listeners) { try { Stop-Process -Id $procId -Force -ErrorAction Stop; Write-Host ('Stopped PID ' + $procId) -ForegroundColor Green } catch { Write-Host ('Could not stop PID ' + $procId + ': ' + $_.Exception.Message) -ForegroundColor Red } }"
echo.
if defined MODE goto end
pause
goto menu

:restart_dashboard
call :stop_quiet
goto start_dashboard

:open_local
start "" "%LOCAL_URL%"
if defined MODE goto end
goto menu

:open_public
start "" "%PUBLIC_URL%"
if defined MODE goto end
goto menu

:logs
echo.
echo --- server.err.log ---
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Test-Path '%ERRLOG%') { Get-Content '%ERRLOG%' -Tail 80 } else { Write-Host 'No stderr log yet.' }"
echo.
echo --- server.out.log ---
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Test-Path '%OUTLOG%') { Get-Content '%OUTLOG%' -Tail 40 } else { Write-Host 'No stdout log yet.' }"
echo.
if defined MODE goto end
pause
goto menu

:restart_cloudflare
echo.
echo Restarting Cloudflared service...
echo This may require running this BAT as Administrator.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { Restart-Service -Name Cloudflared -ErrorAction Stop; Start-Sleep -Seconds 5; $svc = Get-Service Cloudflared; Write-Host ('Cloudflared service: ' + $svc.Status) -ForegroundColor Green } catch { Write-Host ('Cloudflared restart failed: ' + $_.Exception.Message) -ForegroundColor Red }"
echo.
if defined MODE goto end
pause
goto menu

:local_testmode
echo.
echo Switching to LOCAL TESTMODE...
echo This stops the Cloudflared service, so %PUBLIC_URL% should become unreachable.
echo The local dashboard can still run at %LOCAL_URL%.
echo This may require running this BAT as Administrator.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { Stop-Service -Name Cloudflared -ErrorAction Stop; Start-Sleep -Seconds 3; $svc = Get-Service Cloudflared; Write-Host ('Cloudflared service: ' + $svc.Status + ' (LOCAL TESTMODE)') -ForegroundColor Yellow } catch { Write-Host ('Could not enter LOCAL TESTMODE: ' + $_.Exception.Message) -ForegroundColor Red }"
echo.
if defined MODE goto end
pause
goto menu

:live_mode
echo.
echo Switching to LIVE MODE...
echo This starts the Cloudflared service, so %PUBLIC_URL% should become reachable again.
echo This may require running this BAT as Administrator.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { Start-Service -Name Cloudflared -ErrorAction Stop; Start-Sleep -Seconds 6; $svc = Get-Service Cloudflared; Write-Host ('Cloudflared service: ' + $svc.Status + ' (LIVE MODE)') -ForegroundColor Green; try { $public = Invoke-WebRequest -UseBasicParsing '%PUBLIC_URL%/api/status' -TimeoutSec 8; Write-Host ('Cloudflare route: OK ' + $public.StatusCode) -ForegroundColor Green } catch { $code = if ($_.Exception.Response) { $_.Exception.Response.StatusCode.value__ } else { 'n/a' }; Write-Host ('Cloudflare route not ready/failed: ' + $code + ' - ' + $_.Exception.Message) -ForegroundColor Yellow } } catch { Write-Host ('Could not enter LIVE MODE: ' + $_.Exception.Message) -ForegroundColor Red }"
echo.
if defined MODE goto end
pause
goto menu

:stop_quiet
powershell -NoProfile -ExecutionPolicy Bypass -Command "$listeners = Get-NetTCPConnection -LocalPort 5555 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; foreach ($procId in $listeners) { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue }" >nul 2>nul
exit /b 0

:end
endlocal
