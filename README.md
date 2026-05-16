# Wireless Print

Ditch the SD cards and tethered laptops. Wireless Print is a lightweight, high-performance web interface to manage your 3D printer over your local network. Connect your printer to a Raspberry Pi or spare PC via USB, and control it from any browser.

### Features

* Zero-Latency Control: Real-time telemetry and terminal output via WebSockets.
* Non-Blocking Streaming: Background thread processing prevents print stuttering and artifacts.
* Drag & Drop Prints: Upload G-Code (up to 50MB) and print instantly.
* Mobile Ready: A responsive glassmorphism dashboard that scales to any screen.
* Network Resilient: Auto-reconnects seamlessly if your Wi-Fi drops.

### Quick Start

Requires **Python 3.8+** and a printer connected via USB.

**Windows:**
```cmd
.\start.bat
```

**Linux / macOS / Raspberry Pi:**
```bash
chmod +x start.sh
./start.sh
```

*(These scripts automatically handle dependencies via `uv` or `pip` and start the server).*

### Usage

Open your browser to:
[http://localhost:8000](http://localhost:8000)

If running on a network device (like a Raspberry Pi), use its IP address (e.g., `http://192.168.1.100:8000`).

### Troubleshooting

* Port Locked: Ensure Cura, Pronterface, or a slicer isn't holding the serial port open.
* Upload Blocked: The printer must be "Idle" to accept new G-Code files.
* Disconnected: Check the server terminal for hardware connection errors.
