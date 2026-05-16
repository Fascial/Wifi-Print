# Wireless Print

A modern, fast, and sleek web interface to control your 3D printer over serial (USB) from a Raspberry Pi or PC. Designed with a premium "Bento Box" grid layout, it provides a seamless and responsive experience across desktop and mobile devices.

## Features

- **Real-Time Telemetry:** Live updates of printer state, hotend, and bed temperatures via WebSockets.
- **Direct Printing:** Upload G-Code files directly to the server (up to 50MB) and print instantly.
- **Robust Motion Controls:** Jog the printer with selectable step sizes (0.1mm, 1mm, 10mm, 50mm) and home individual or all axes.
- **Live Terminal:** Monitor the raw serial output from your printer and send custom G-Code commands instantly.
- **Print Management:** Pause, resume, and safely cancel active prints with confirmation dialogues and proper G-Code flow control.
- **Responsive "Bento" Design:** A beautiful, dark-mode glassmorphism UI that automatically adapts to any screen size—from large desktop monitors to smartphones.
- **Network Resilient:** Automatic WebSocket reconnection and intelligent polling ensure the UI recovers gracefully from network drops or server restarts.
- **Persistent Settings:** Automatically saves your last used COM port and connection settings.

## Prerequisites

- **Python 3.8+**
- A 3D printer connected via USB (serial).
- **uv** (recommended for fast dependency management and environment handling) or standard `pip`.

## Quick Start

### Windows

The easiest way to start the server on Windows is by using the included batch script:

```cmd
.\start.bat
```

This script will automatically synchronize dependencies using `uv` and start the server.

### Linux / macOS / Raspberry Pi

To run the server manually using `uv`, open your terminal in the project folder and run:

```bash
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

> **Note:** If you are not using `uv`, you can install the dependencies via pip:
> `pip install fastapi uvicorn pyserial websockets python-multipart`
> Then start the server:
> `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`

## Accessing the Interface

Once the server is running, open your web browser and navigate to:

[http://localhost:8000](http://localhost:8000)

*(If you are running this on a headless device like a Raspberry Pi on your local network, replace `localhost` with the device's IP address, e.g., `http://192.168.1.100:8000`).*

## Architecture & Technology Stack

- **Backend:** FastAPI handles the HTTP API routes and WebSocket connections asynchronously.
- **Hardware Interface:** `pyserial` runs in a dedicated background thread executor to prevent blocking the main asyncio event loop, using thread-safe queues and locks for robust G-Code streaming.
- **Frontend:** Pure Vanilla HTML, CSS, and JS. Designed for high performance without the overhead of heavy frontend frameworks.
- **Communication:** Bi-directional WebSockets (`/ws/telemetry` and `/ws/terminal`) provide zero-latency updates to the client.

## Troubleshooting

- **Cannot Connect to Printer:** Ensure the printer is plugged in and turned on. Verify the correct COM port (Windows) or `/dev/ttyUSB*` (Linux) is selected. Close any other software (like Cura or Pronterface) that might be holding the serial port open.
- **UI Disconnected:** The UI will automatically attempt to reconnect. If it fails, verify the terminal output where the server is running to check for backend errors or network issues.
- **File Upload Fails:** Ensure the printer is in the `Idle` state before attempting to upload a G-Code file. The server enforces a 50MB limit to prevent out-of-memory errors on small SBCs like the Raspberry Pi Zero.

## License

This project is provided as-is for the 3D printing community. Feel free to fork, modify, and improve!
