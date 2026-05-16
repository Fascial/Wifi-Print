@echo off
echo Starting WiFi Print Controller...

where uv >nul 2>&1
if %ERRORLEVEL% == 0 (
    echo uv is installed. Using uv to run the server...
    uv sync
    uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
    exit /b %ERRORLEVEL%
)

echo uv not found. Falling back to venv and pip...
if not exist "venv\" (
    echo Creating virtual environment...
    python -m venv venv
)
call venv\Scripts\activate.bat
echo Installing dependencies...
pip install fastapi uvicorn pyserial websockets python-multipart
uvicorn app.main:app --host 0.0.0.0 --port 8000
