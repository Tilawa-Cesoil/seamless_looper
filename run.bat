@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

if defined LOOPSTUDIO_FFMPEG goto ffmpeg_ok
if defined XIANYIN_FFMPEG goto ffmpeg_ok
if exist "%~dp0bin\ffmpeg.exe" goto ffmpeg_ok
if exist "%~dp0ffmpeg\bin\ffmpeg.exe" goto ffmpeg_ok
if exist "%~dp0vendor\ffmpeg\bin\ffmpeg.exe" goto ffmpeg_ok

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo WARNING: ffmpeg was not found.
    echo Install it on PATH, put ffmpeg.exe/ffprobe.exe in bin\, or set
    echo LOOPSTUDIO_FFMPEG and LOOPSTUDIO_FFPROBE.
    echo.
)

:ffmpeg_ok
python app.py %*
if errorlevel 1 pause
endlocal
