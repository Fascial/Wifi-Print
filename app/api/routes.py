from fastapi import APIRouter, UploadFile, File
import logging
from typing import Dict, Any

from app.core.models import ConnectRequest, CommandRequest
from app.core.printer_instance import printer

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/api/ports")
async def get_ports():
    return {"ports": printer.get_available_ports()}

@router.post("/api/connect")
async def connect_printer(req: ConnectRequest):
    success, msg = await printer.connect(req.port, req.baudrate)
    return {"success": success, "message": msg, "state": printer.get_state()}

@router.post("/api/disconnect")
async def disconnect_printer():
    printer.disconnect()
    return {"success": True, "state": printer.get_state()}

@router.post("/api/command")
async def send_command(req: CommandRequest):
    printer.send_command(req.command)
    return {"success": True}

@router.post("/api/upload")
async def upload_gcode(file: UploadFile = File(...)):
    if not file.filename.endswith('.gcode'):
        return {"success": False, "error": "Invalid file type. Must be .gcode"}
    
    content = await file.read()
    gcode_text = content.decode('utf-8', errors='ignore')
    
    success = printer.start_print(file.filename, gcode_text)
    return {"success": success, "filename": file.filename}

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
    printer.cancel_print()
    return {"success": True}

@router.post("/api/control/speed/{percent}")
async def control_speed(percent: int):
    printer.send_command(f"M220 S{percent}")
    return {"success": True}

@router.post("/api/control/flow/{percent}")
async def control_flow(percent: int):
    printer.send_command(f"M221 S{percent}")
    return {"success": True}
