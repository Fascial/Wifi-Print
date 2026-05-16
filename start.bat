@echo off
echo Starting WiFi Print Controller...

where uv >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo uv is not installed. Please install uv first:
    echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    exit /b 1
)

uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
