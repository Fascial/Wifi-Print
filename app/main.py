from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import socket
from contextlib import asynccontextmanager
import json
import os
import logging

from app.api.routes import router
from app.api.websockets import telemetry_mgr, terminal_mgr
from app.core.printer_instance import printer

logger = logging.getLogger(__name__)

# Resolve paths relative to this file, not CWD
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

@asynccontextmanager
async def lifespan(app: FastAPI):
    local_ip = get_local_ip()
    print("\n" + "="*50)
    print(" Wireless Print Server Running! ")
    print(f" Local Access:   http://localhost:8000")
    print(f" Network Access: http://{local_ip}:8000")
    print("="*50 + "\n")

    # Register ONE global callback for telemetry + terminal
    async def on_telemetry(state_data):
        await telemetry_mgr.broadcast(json.dumps(state_data))

    async def on_terminal(text):
        await terminal_mgr.broadcast(text)

    printer.telemetry_callbacks.append(on_telemetry)
    printer.terminal_callbacks.append(on_terminal)

    yield

    # Shutdown: C3 - await async disconnect, L3 - no redundant close_port()
    printer.telemetry_callbacks.clear()
    printer.terminal_callbacks.clear()
    await printer.disconnect()

app = FastAPI(title="Wireless Print", lifespan=lifespan)

# CORS middleware for LAN access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure static directory exists
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Include REST routes
app.include_router(router)

@app.get("/")
async def get_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    return FileResponse(index_path)

# --- WebSockets (H4 - catch all exceptions, not just WebSocketDisconnect) ---

@app.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    await telemetry_mgr.connect(websocket)
    try:
        # Send initial state immediately
        await websocket.send_text(json.dumps(printer.get_state()))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        telemetry_mgr.disconnect(websocket)
    except Exception as e:
        telemetry_mgr.disconnect(websocket)
        logger.warning(f"Telemetry WS error: {e}")

@app.websocket("/ws/terminal")
async def websocket_terminal(websocket: WebSocket):
    await terminal_mgr.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        terminal_mgr.disconnect(websocket)
    except Exception as e:
        terminal_mgr.disconnect(websocket)
        logger.warning(f"Terminal WS error: {e}")
