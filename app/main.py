from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import json
import os

from app.api.routes import router
from app.api.websockets import telemetry_mgr, terminal_mgr
from app.core.printer_instance import printer

# Resolve paths relative to this file, not CWD (Bug #17)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Register ONE global callback for telemetry + terminal (Bug #6 — O(N²) fix)
    async def on_telemetry(state_data):
        await telemetry_mgr.broadcast(json.dumps(state_data))

    async def on_terminal(text):
        await terminal_mgr.broadcast(text)

    printer.telemetry_callbacks.append(on_telemetry)
    printer.terminal_callbacks.append(on_terminal)

    yield

    # Shutdown: CRITICAL for preventing PermissionError(13) zombie locks on Windows
    printer.telemetry_callbacks.clear()
    printer.terminal_callbacks.clear()
    printer.disconnect()
    printer.close_port()

app = FastAPI(title="Wireless Print", lifespan=lifespan)

# CORS middleware for LAN access (Bug #16)
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
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

# --- WebSockets (Bug #6 — simplified, no per-client callback registration) ---

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

@app.websocket("/ws/terminal")
async def websocket_terminal(websocket: WebSocket):
    await terminal_mgr.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        terminal_mgr.disconnect(websocket)
