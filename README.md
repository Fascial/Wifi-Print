# Antigravity Print

A modern, fast, and sleek web interface to control your 3D printer over serial (USB) from a Raspberry Pi or PC.

## Quick Start

To run the server, simply open your terminal in this project folder and run:

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

> **Note:** If you haven't installed the dependencies yet, you can run `uv add fastapi uvicorn pyserial websockets python-multipart` first.

Once the server is running, open your web browser and go to:
[http://localhost:8000](http://localhost:8000)

(If you are running this on a Raspberry Pi, replace `localhost` with the Pi's IP address, e.g., `http://192.168.1.100:8000`).
