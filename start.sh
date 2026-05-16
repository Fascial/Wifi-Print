#!/bin/bash
echo "Starting WiFi Print Controller..."

if command -v uv &> /dev/null
then
    echo "uv is installed. Using uv to run the server..."
    uv add fastapi uvicorn pyserial websockets python-multipart
    uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
    exit $?
fi

echo "uv not found. Falling back to venv and pip..."
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi
source venv/bin/activate
echo "Installing dependencies..."
pip install fastapi uvicorn pyserial websockets python-multipart
uvicorn app.main:app --host 0.0.0.0 --port 8000
