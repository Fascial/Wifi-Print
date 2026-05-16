import asyncio
import serial
import serial.tools.list_ports
import time
import logging
import re
from typing import List, Optional, Dict, Any, Tuple

from app.core.models import PrinterState

logger = logging.getLogger(__name__)

class PrinterController:
    def __init__(self):
        self.serial_conn: Optional[serial.Serial] = None
        self.state = PrinterState.DISCONNECTED
        
        self.current_file: Optional[str] = None
        self.gcode_lines: List[str] = []
        self.current_line_idx = 0
        
        # Telemetry
        self.temps = {"hotend": {"actual": 0.0, "target": 0.0}, "bed": {"actual": 0.0, "target": 0.0}}
        self.position = {"X": 0.0, "Y": 0.0, "Z": 0.0}
        
        # Time Analytics
        self.print_start_time: Optional[float] = None
        self.estimated_total_time: Optional[float] = None
        
        self.terminal_callbacks = []
        self.telemetry_callbacks = []
        
        # Concurrency primitives
        self.manual_queue: Optional[asyncio.Queue] = None
        self._ok_event: Optional[asyncio.Event] = None
        self._read_task: Optional[asyncio.Task] = None
        self._print_task: Optional[asyncio.Task] = None
        self._telemetry_task: Optional[asyncio.Task] = None

    @staticmethod
    def get_available_ports() -> List[str]:
        return [port.device for port in serial.tools.list_ports.comports()]

    async def connect(self, port: str, baudrate: int) -> Tuple[bool, str]:
        try:
            if self.manual_queue is None:
                self.manual_queue = asyncio.Queue()
            if self._ok_event is None:
                self._ok_event = asyncio.Event()

            # Attempt serial connection in an executor to avoid blocking the event loop
            loop = asyncio.get_running_loop()
            def _open_port():
                return serial.Serial(port, baudrate, timeout=0.1)
                
            self.serial_conn = await loop.run_in_executor(None, _open_port)
            self.state = PrinterState.IDLE
            self._ok_event.set() # Assume ready initially
            
            # Start background tasks
            self._read_task = asyncio.create_task(self._read_loop())
            self._telemetry_task = asyncio.create_task(self._telemetry_loop())
            self._notify_telemetry()
            return True, "Connected successfully"
            
        except PermissionError:
            self.state = PrinterState.ERROR
            return False, f"Port {port} is locked by another program. Close Cura/PrusaSlicer, or unplug and replug the USB."
        except serial.SerialException as e:
            self.state = PrinterState.ERROR
            return False, f"Serial error: {e}"
        except Exception as e:
            self.state = PrinterState.ERROR
            return False, f"Unknown error: {e}"

    def disconnect(self):
        self.state = PrinterState.DISCONNECTED
        if self._read_task: self._read_task.cancel()
        if self._print_task: self._print_task.cancel()
        if self._telemetry_task: self._telemetry_task.cancel()
        
        self.close_port()
        self._notify_telemetry()
        
    def close_port(self):
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
            except Exception as e:
                logger.error(f"Error closing port: {e}")
        self.serial_conn = None

    def send_command(self, cmd: str, is_manual: bool = True):
        if not self.serial_conn or not self.serial_conn.is_open:
            return
        
        cmd = cmd.strip()
        if not cmd: return
        
        if is_manual and self.manual_queue:
            self.manual_queue.put_nowait(cmd)
        else:
            self._write_serial(cmd)

    def _write_serial(self, cmd: str):
        if self.serial_conn and self.serial_conn.is_open:
            try:
                full_cmd = f"{cmd}\n"
                self.serial_conn.write(full_cmd.encode('utf-8'))
                self._emit_terminal(f"> {cmd}")
            except Exception as e:
                logger.error(f"Write error: {e}")

    async def _read_loop(self):
        while self.state != PrinterState.DISCONNECTED and self.serial_conn:
            try:
                if self.serial_conn.in_waiting > 0:
                    line = self.serial_conn.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        self._handle_response(line)
                else:
                    await asyncio.sleep(0.01)
                    
                if self.manual_queue and not self.manual_queue.empty() and self._ok_event and self._ok_event.is_set():
                    cmd = self.manual_queue.get_nowait()
                    self._ok_event.clear()
                    self._write_serial(cmd)
                    
            except serial.SerialException as e:
                logger.error(f"Serial disconnected: {e}")
                self.disconnect()
                break
            except Exception as e:
                logger.error(f"Read error: {e}")
                await asyncio.sleep(1)

    def _handle_response(self, line: str):
        self._emit_terminal(line)
        
        if line.startswith("ok") and self._ok_event:
            self._ok_event.set()
        
        if "T:" in line or "B:" in line:
            self._parse_temperature(line)

    def _parse_temperature(self, line: str):
        try:
            parts = line.split()
            for p in parts:
                if p.startswith("T:"):
                    self.temps["hotend"]["actual"] = float(p.split(":")[1])
                elif p.startswith("B:"):
                    self.temps["bed"]["actual"] = float(p.split(":")[1])
            self._notify_telemetry()
        except Exception:
            pass

    async def _telemetry_loop(self):
        while self.state != PrinterState.DISCONNECTED:
            if self.state in [PrinterState.IDLE, PrinterState.PRINTING, PrinterState.PAUSED]:
                self.send_command("M105")
            await asyncio.sleep(2)

    def _parse_metadata(self, gcode: str):
        # Search for typical slicer time estimates (e.g. Cura: ; TIME:3600)
        time_match = re.search(r';\s*TIME:(\d+)', gcode)
        if time_match:
            self.estimated_total_time = float(time_match.group(1))
        else:
            self.estimated_total_time = None

    def start_print(self, filename: str, gcode: str):
        if self.state != PrinterState.IDLE:
            return False
            
        self._parse_metadata(gcode)
        self.current_file = filename
        self.gcode_lines = [l.strip() for l in gcode.splitlines() if l.strip() and not l.strip().startswith(';')]
        self.current_line_idx = 0
        self.print_start_time = time.time()
        
        self.state = PrinterState.PRINTING
        self._notify_telemetry()
        
        if self._print_task:
            self._print_task.cancel()
        self._print_task = asyncio.create_task(self._print_loop())
        return True

    def pause_print(self):
        if self.state == PrinterState.PRINTING:
            self.state = PrinterState.PAUSED
            self.send_command("M25")
            self._notify_telemetry()

    def resume_print(self):
        if self.state == PrinterState.PAUSED:
            self.state = PrinterState.PRINTING
            self.send_command("M24")
            self._notify_telemetry()

    def cancel_print(self):
        if self.state in [PrinterState.PRINTING, PrinterState.PAUSED]:
            if self._print_task:
                self._print_task.cancel()
            self.state = PrinterState.IDLE
            self.current_file = None
            self.gcode_lines = []
            self.current_line_idx = 0
            self.print_start_time = None
            self.estimated_total_time = None
            
            self.send_command("M104 S0")
            self.send_command("M140 S0")
            self.send_command("G28 X Y")
            
            self._notify_telemetry()

    async def _print_loop(self):
        try:
            while self.state == PrinterState.PRINTING and self.current_line_idx < len(self.gcode_lines):
                if self._ok_event:
                    await self._ok_event.wait()
                
                if self.manual_queue and not self.manual_queue.empty():
                    await asyncio.sleep(0.01)
                    continue

                if self.state != PrinterState.PRINTING:
                    break

                line = self.gcode_lines[self.current_line_idx]
                self.current_line_idx += 1
                
                if self._ok_event:
                    self._ok_event.clear()
                self._write_serial(line)
                
                if self.current_line_idx % 10 == 0:
                    self._notify_telemetry()
                    
            if self.current_line_idx >= len(self.gcode_lines):
                self.state = PrinterState.IDLE
                self.print_start_time = None
                self._notify_telemetry()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Print error: {e}")
            self.state = PrinterState.ERROR
            self._notify_telemetry()

    def get_state(self) -> Dict[str, Any]:
        progress = 0
        elapsed = 0
        eta = None
        
        if self.gcode_lines:
            progress = (self.current_line_idx / len(self.gcode_lines)) * 100
            
        if self.print_start_time and self.state in [PrinterState.PRINTING, PrinterState.PAUSED]:
            elapsed = time.time() - self.print_start_time
            
            # Simple ETA extrapolation
            if progress > 1.0: # Wait for at least 1% for stable extrapolation
                total_extrapolated = (elapsed / progress) * 100
                eta = total_extrapolated - elapsed
            elif self.estimated_total_time:
                eta = max(0, self.estimated_total_time - elapsed)
            
        return {
            "state": self.state,
            "file": self.current_file,
            "progress": progress,
            "elapsed_s": int(elapsed),
            "eta_s": int(eta) if eta is not None else None,
            "temps": self.temps,
            "position": self.position
        }

    def _notify_telemetry(self):
        state_data = self.get_state()
        for cb in self.telemetry_callbacks:
            asyncio.create_task(cb(state_data))

    def _emit_terminal(self, text: str):
        for cb in self.terminal_callbacks:
            asyncio.create_task(cb(text))

