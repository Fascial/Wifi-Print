from fastapi import APIRouter, UploadFile, File
import logging
import os
import aiofiles
from typing import Dict, Any

from app.core.models import ConnectRequest, CommandRequest, PrinterState
from app.core.printer_instance import printer

router = APIRouter()
logger = logging.getLogger(__name__)

# 50MB upload limit
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

@router.get("/api/ports")
async def get_ports():
    return {"ports": printer.get_available_ports()}

@router.get("/api/state")
async def get_state():
    return printer.get_state()

@router.post("/api/connect")
async def connect_printer(req: ConnectRequest):
    success, msg = await printer.connect(req.port, req.baudrate)
    return {"success": success, "message": msg, "state": printer.get_state()}

@router.post("/api/disconnect")
async def disconnect_printer():
    # C3: disconnect() is now async
    await printer.disconnect()
    return {"success": True, "state": printer.get_state()}

@router.post("/api/command")
async def send_command(req: CommandRequest):
    printer.send_command(req.command)
    return {"success": True}

@router.post("/api/upload")
async def upload_gcode(file: UploadFile = File(...)):
    if printer.state != PrinterState.IDLE:
        return {"success": False, "error": "Printer is not idle. Cancel current print first."}

    if not file.filename.endswith('.gcode'):
        return {"success": False, "error": "Invalid file type. Must be .gcode"}

    filepath = os.path.join(UPLOAD_DIR, file.filename)
    total = 0
    too_large = False
    try:
        async with aiofiles.open(filepath, 'wb') as out_file:
            while True:
                chunk = await file.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    too_large = True
                    break
                await out_file.write(chunk)
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        return {"success": False, "error": "Failed to save file."}

    if too_large:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception as e:
                logger.error(f"Failed to remove oversized file: {e}")
        return {"success": False, "error": f"File too large. Max {MAX_UPLOAD_BYTES // (1024*1024)}MB."}

    # Set as currently loaded file, do NOT start print automatically
    printer.loaded_file = file.filename
    printer.loaded_filepath = filepath
    printer._safe_notify_telemetry()
    return {"success": True, "filename": file.filename}

@router.post("/api/control/start")
async def control_start():
    if not printer.loaded_file or not printer.loaded_filepath:
        return {"success": False, "error": "No file loaded"}
    
    if printer.state != PrinterState.IDLE:
        return {"success": False, "error": "Printer is not idle"}

    success = await printer.start_print(printer.loaded_file, printer.loaded_filepath)
    return {"success": success}

@router.post("/api/control/unload")
async def control_unload():
    if printer.state != PrinterState.IDLE:
        return {"success": False, "error": "Printer is not idle"}
    
    filepath = printer.loaded_filepath
    printer.loaded_file = None
    printer.loaded_filepath = None
    printer._safe_notify_telemetry()
    
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception as e:
            logger.error(f"Failed to delete unloaded file: {e}")
            
    return {"success": True}

@router.post("/api/control/pause")
async def control_pause():
    printer.pause_print()
    return {"success": True}

@router.post("/api/control/resume")
async def control_resume():
    printer.resume_print()
    return {"success": True}

@router.post("/api/control/cancel")
async def control_cancel():
    # cancel_print is now async
    await printer.cancel_print()
    return {"success": True}

# M4: Server-side validation on speed/flow percent
@router.post("/api/control/speed/{percent}")
async def control_speed(percent: int):
    if not (10 <= percent <= 300):
        return {"success": False, "error": "Speed must be between 10% and 300%"}
    printer.send_command(f"M220 S{percent}")
    return {"success": True}

@router.post("/api/control/flow/{percent}")
async def control_flow(percent: int):
    if not (10 <= percent <= 200):
        return {"success": False, "error": "Flow rate must be between 10% and 200%"}
    printer.send_command(f"M221 S{percent}")
    return {"success": True}
