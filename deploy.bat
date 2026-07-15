@echo off
rem ══════════════════════════════════════════════════════════════════
rem  TestGen v2.2 — разворачивание в Docker (Windows)
rem
rem  Использование:
rem    deploy.bat            — собрать и запустить (эквивалент "up")
rem    deploy.bat up
rem    deploy.bat down       — остановить и удалить контейнер
rem    deploy.bat logs       — логи контейнера
rem    deploy.bat status     — состояние контейнера
rem
rem  Порт на хосте: set TESTGEN_PORT=9000 перед запуском (по умолчанию 8000)
rem ══════════════════════════════════════════════════════════════════
setlocal
cd /d "%~dp0"

set "IMAGE=testgen:2.4"
set "CONTAINER=testgen"
if "%TESTGEN_PORT%"=="" (set "PORT=8000") else (set "PORT=%TESTGEN_PORT%")
if "%TESTGEN_BIND%"=="" (set "BIND=0.0.0.0") else (set "BIND=%TESTGEN_BIND%")
set "CMD=%~1"
if "%CMD%"=="" set "CMD=up"

where docker >nul 2>nul
if errorlevel 1 (
  echo [x] Docker не найден. Установите Docker Desktop: https://docs.docker.com/get-docker/
  exit /b 1
)
docker info >nul 2>nul
if errorlevel 1 (
  echo [x] Docker-демон не запущен. Запустите Docker Desktop и повторите.
  exit /b 1
)

if /i "%CMD%"=="up"     goto :up
if /i "%CMD%"=="down"   goto :down
if /i "%CMD%"=="logs"   goto :logs
if /i "%CMD%"=="status" goto :status
echo Использование: %~nx0 {up^|down^|logs^|status}
exit /b 1

:up
for %%F in (Dockerfile requirements.txt test_gen.py web_app.py md2pdf.py index.html) do (
  if not exist "%%F" (
    echo [x] Не найден файл: %%F — запускайте из папки проекта
    exit /b 1
  )
)
echo [*] Сборка образа %IMAGE%...
docker build -t %IMAGE% . || exit /b 1
docker rm -f %CONTAINER% >nul 2>nul
echo [*] Запуск контейнера: %BIND%:%PORT% -^> 8000...
docker run -d --name %CONTAINER% --restart unless-stopped --read-only --tmpfs /tmp ^
  --security-opt no-new-privileges:true ^
  -p %BIND%:%PORT%:8000 %IMAGE% >nul || exit /b 1
echo.
echo [v] TestGen развёрнут
echo     На этой машине:  http://127.0.0.1:%PORT%
if "%BIND%"=="0.0.0.0" (
  echo     Из вашей сети:   http://^<IP-этой-машины^>:%PORT%  ^(узнать IP: ipconfig^)
  echo     Ограничить доступ: set TESTGEN_BIND=127.0.0.1 ^&^& deploy.bat up
)
echo     Логи:      deploy.bat logs
echo     Остановка: deploy.bat down
start "" http://127.0.0.1:%PORT%
exit /b 0

:down
docker rm -f %CONTAINER% >nul 2>nul
echo [v] Контейнер остановлен и удалён
exit /b 0

:logs
docker logs -f %CONTAINER%
exit /b 0

:status
docker ps -a --filter "name=%CONTAINER%" --format "Контейнер: {{.Names}}  Статус: {{.Status}}  Порты: {{.Ports}}"
exit /b 0
