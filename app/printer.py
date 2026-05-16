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
        self._serial_lock: Optional[asyncio.Lock] = None
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
            if self.serial_conn and self.serial_conn.is_open:
                if self.serial_conn.port == port:
                    return True, "Already connected"
                else:
                    self.disconnect()
            
            # Always create fresh primitives on connect (Bug #13)
            self.manual_queue = asyncio.Queue()
            self._ok_event = asyncio.Event()
            self._serial_lock = asyncio.Lock()

            # Non-blocking serial open (Bug #2)
            loop = asyncio.get_running_loop()
            def _open_port():
                return serial.Serial(port, baudrate, timeout=0.1)
                
            self.serial_conn = await loop.run_in_executor(None, _open_port)
            self.state = PrinterState.IDLE
            self._ok_event.set()
            
            # Start background tasks
            self._read_task = asyncio.create_task(self._read_loop())
            self._telemetry_task = asyncio.create_task(self._telemetry_loop())
            self._safe_notify_telemetry()
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
        self._safe_notify_telemetry()
        
    def close_port(self):
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
            except Exception as e:
                logger.error(f"Error closing port: {e}")
        self.serial_conn = None

    def send_command(self, cmd: str, is_manual: bool = True):
        """Queue or directly send a G-Code command. Splits multi-line commands (Bug #11)."""
        if not self.serial_conn or not self.serial_conn.is_open:
            return
        
        cmd = cmd.strip()
        if not cmd: return
        
        # Split multi-line commands into individual lines
        lines = cmd.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if is_manual and self.manual_queue:
                self.manual_queue.put_nowait(line)
            else:
                self._write_serial(line)

    def _write_serial(self, cmd: str):
        """Synchronous serial write for immediate/cancel commands."""
        if self.serial_conn and self.serial_conn.is_open:
            try:
                full_cmd = f"{cmd}\n"
                self.serial_conn.write(full_cmd.encode('utf-8'))
                # Don't emit M105 to terminal (Bug #19 - terminal spam)
                if cmd.strip() != "M105":
                    self._emit_terminal(f"> {cmd}")
            except Exception as e:
                logger.error(f"Write error: {e}")

    async def _write_serial_locked(self, cmd: str):
        """Async serial write with lock for concurrent safety (Bug #3)."""
        if not self.serial_conn or not self.serial_conn.is_open:
            return
        if self._serial_lock:
            async with self._serial_lock:
                loop = asyncio.get_running_loop()
                try:
                    full_cmd = f"{cmd}\n"
                    await loop.run_in_executor(
                        None, self.serial_conn.write, full_cmd.encode('utf-8')
                    )
                    if cmd.strip() != "M105":
                        self._emit_terminal(f"> {cmd}")
                except Exception as e:
                    logger.error(f"Write error: {e}")

    async def _read_loop(self):
        """Background serial read loop using executor to avoid blocking (Bug #2)."""
        loop = asyncio.get_running_loop()
        while self.state != PrinterState.DISCONNECTED and self.serial_conn:
            try:
                # Non-blocking serial read via executor
                def _read_serial():
                    if self.serial_conn and self.serial_conn.in_waiting > 0:
                        return self.serial_conn.readline().decode('utf-8', errors='ignore').strip()
                    return None
                
                line = await loop.run_in_executor(None, _read_serial)
                if line:
                    self._handle_response(line)
                else:
                    await asyncio.sleep(0.01)
                    
                # Process manual queue when printer is ready
                if (self.manual_queue and not self.manual_queue.empty()
                        and self._ok_event and self._ok_event.is_set()):
                    cmd = self.manual_queue.get_nowait()
                    self._ok_event.clear()
                    await self._write_serial_locked(cmd)
                    
            except serial.SerialException as e:
                logger.error(f"Serial disconnected: {e}")
                self.disconnect()
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Read error: {e}")
                await asyncio.sleep(1)

    def _handle_response(self, line: str):
        # Filter M105 temperature reports from terminal display (Bug #19)
        is_temp_report = "T:" in line and ("B:" in line or "/" in line)
        if not is_temp_report:
            self._emit_terminal(line)
        
        if line.startswith("ok") and self._ok_event:
            self._ok_event.set()
        
        if "T:" in line or "B:" in line:
            self._parse_temperature(line)

    def _parse_temperature(self, line: str):
        """Parse actual AND target temperatures from Marlin M105 response (Bug #9).
        Format: ok T:200.5 /210.0 B:60.2 /60.0
        """
        try:
            # Try to match actual/target pairs first
            hotend_match = re.search(r'T:(\d+\.?\d*)\s*/(\d+\.?\d*)', line)
            bed_match = re.search(r'B:(\d+\.?\d*)\s*/(\d+\.?\d*)', line)
            
            if hotend_match:
                self.temps["hotend"]["actual"] = float(hotend_match.group(1))
                self.temps["hotend"]["target"] = float(hotend_match.group(2))
            else:
                t_match = re.search(r'T:(\d+\.?\d*)', line)
                if t_match:
                    self.temps["hotend"]["actual"] = float(t_match.group(1))
            
            if bed_match:
                self.temps["bed"]["actual"] = float(bed_match.group(1))
                self.temps["bed"]["target"] = float(bed_match.group(2))
            else:
                b_match = re.search(r'B:(\d+\.?\d*)', line)
                if b_match:
                    self.temps["bed"]["actual"] = float(b_match.group(1))
            
            self._safe_notify_telemetry()
        except Exception:
            pass

    async def _telemetry_loop(self):
        """Periodic temperature polling. Bypasses manual queue (Bug #8)."""
        while self.state != PrinterState.DISCONNECTED:
            if self.state in [PrinterState.IDLE, PrinterState.PRINTING, PrinterState.PAUSED]:
                # Write M105 directly with lock, bypassing manual queue
                await self._write_serial_locked("M105")
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
        self._safe_notify_telemetry()
        
        if self._print_task:
            self._print_task.cancel()
        self._print_task = asyncio.create_task(self._print_loop())
        return True

    def pause_print(self):
        """Pause host-streamed print by stopping the print loop (Bug #5 — no M25)."""
        if self.state == PrinterState.PRINTING:
            self.state = PrinterState.PAUSED
            self._safe_notify_telemetry()

    def resume_print(self):
        """Resume print by restarting the print loop (Bug #5 — no M24)."""
        if self.state == PrinterState.PAUSED:
            self.state = PrinterState.PRINTING
            self._safe_notify_telemetry()
            # Restart the print loop since it exited when state changed to PAUSED
            if self._print_task:
                self._print_task.cancel()
            self._print_task = asyncio.create_task(self._print_loop())

    def cancel_print(self):
        """Cancel print with proper cleanup (Bug #4)."""
        if self.state in [PrinterState.PRINTING, PrinterState.PAUSED]:
            if self._print_task:
                self._print_task.cancel()
            self.state = PrinterState.IDLE
            self.current_file = None
            self.gcode_lines = []
            self.current_line_idx = 0
            self.print_start_time = None
            self.estimated_total_time = None
            
            # Drain stale manual commands before sending cancel sequence
            if self.manual_queue:
                while not self.manual_queue.empty():
                    try:
                        self.manual_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
            
            # Direct writes for cancel commands — tasks are already cancelled
            self._write_serial("M104 S0")
            self._write_serial("M140 S0")
            self._write_serial("G28 X Y")
            
            self._safe_notify_telemetry()

    async def _print_loop(self):
        try:
            while self.state == PrinterState.PRINTING and self.current_line_idx < len(self.gcode_lines):
                if self._ok_event:
                    await self._ok_event.wait()
                
                # Yield to manual commands
                if self.manual_queue and not self.manual_queue.empty():
                    await asyncio.sleep(0.01)
                    continue

                if self.state != PrinterState.PRINTING:
                    break

                line = self.gcode_lines[self.current_line_idx]
                self.current_line_idx += 1
                
                if self._ok_event:
                    self._ok_event.clear()
                await self._write_serial_locked(line)
                
                if self.current_line_idx % 10 == 0:
                    self._safe_notify_telemetry()
                    
            # Print completed successfully
            if self.state == PrinterState.PRINTING and self.current_line_idx >= len(self.gcode_lines):
                self.state = PrinterState.IDLE
                self.current_file = None
                self.print_start_time = None
                self._safe_notify_telemetry()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Print error: {e}")
            self.state = PrinterState.ERROR
            self._safe_notify_telemetry()

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
            "port": self.serial_conn.port if self.serial_conn else None,
            "file": self.current_file,
            "progress": progress,
            "elapsed_s": int(elapsed),
            "eta_s": int(eta) if eta is not None else None,
            "temps": self.temps,
            "position": self.position
        }

    def _safe_notify_telemetry(self):
        """Safely emit telemetry — handles missing event loop (Bug #1)."""
        state_data = self.get_state()
        for cb in self.telemetry_callbacks:
            try:
                asyncio.get_running_loop()
                asyncio.create_task(cb(state_data))
            except RuntimeError:
                pass  # No running event loop (e.g., during shutdown)

    def _emit_terminal(self, text: str):
        """Safely emit terminal text — handles missing event loop (Bug #1)."""
        for cb in self.terminal_callbacks:
            try:
                asyncio.get_running_loop()
                asyncio.create_task(cb(text))
            except RuntimeError:
                pass
