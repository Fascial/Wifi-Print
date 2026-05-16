from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import json
import os

from app.api.routes import router
from app.api.websockets import telemetry_mgr, terminal_mgr
from app.core.printer_instance import printer

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    yield
    # Shutdown actions: CRITICAL for preventing PermissionError(13) zombie locks on Windows
    printer.disconnect()
    printer.close_port()

app = FastAPI(title="Wireless Print", lifespan=lifespan)

# Ensure static directory exists
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include REST routes
app.include_router(router)

@app.get("/")
async def get_index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

# --- WebSockets ---

@app.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    await telemetry_mgr.connect(websocket)
    try:
        # Send initial state
        await websocket.send_text(json.dumps(printer.get_state()))
        
        async def telemetry_callback(state_data):
            await telemetry_mgr.broadcast(json.dumps(state_data))
            
        printer.telemetry_callbacks.append(telemetry_callback)
        
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        telemetry_mgr.disconnect(websocket)
        if telemetry_callback in printer.telemetry_callbacks:
            printer.telemetry_callbacks.remove(telemetry_callback)

@app.websocket("/ws/terminal")
async def websocket_terminal(websocket: WebSocket):
    await terminal_mgr.connect(websocket)
    try:
        async def terminal_callback(text):
            await terminal_mgr.broadcast(text)
            
        printer.terminal_callbacks.append(terminal_callback)
        
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        terminal_mgr.disconnect(websocket)
        if terminal_callback in printer.terminal_callbacks:
            printer.terminal_callbacks.remove(terminal_callback)
