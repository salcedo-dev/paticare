@echo off
chcp 65001 >nul
title Pati - no cierres esta ventana
cd /d "%~dp0"
set PYTHONUTF8=1

rem Evita abrir dos Patis a la vez (WhatsApp se confunde y puede cerrar la sesion)
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'main\.py' }) { exit 1 }"
if errorlevel 1 (
    echo.
    echo  Pati ya esta encendida en otra ventana. No abras dos a la vez.
    echo  Si no la ves, cierrala con: taskkill /IM python.exe /F
    echo.
    pause
    exit /b
)

:inicio
echo.
echo ===== Pati encendida: %date% %time% =====
echo  Para apagarla: Ctrl+C y luego S, o cierra esta ventana.
echo.
venv\Scripts\python.exe main.py
echo.
echo  Pati se detuvo. Se reinicia sola en 10 segundos (Ctrl+C para cancelar)...
timeout /t 10 /nobreak >nul
goto inicio
