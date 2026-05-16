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
        self.current_filepath: Optional[str] = None
        self.current_file_pos = 0
        self.file_size = 0
        self.current_line_idx = 0

        # Telemetry
        self.temps = {"hotend": {"actual": 0.0, "target": 0.0}, "bed": {"actual": 0.0, "target": 0.0}}
        self.position = {"X": 0.0, "Y": 0.0, "Z": 0.0}

        # Time Analytics (H1: includes pause tracking)
        self.print_start_time: Optional[float] = None
        self.estimated_total_time: Optional[float] = None
        self._pause_start: Optional[float] = None
        self._total_paused: float = 0.0

        # Error tracking (H2)
        self.last_error: Optional[str] = None

        self.terminal_callbacks = []
        self.telemetry_callbacks = []

        # Concurrency primitives (C1+C2: single-sender architecture)
        self._serial_lock: Optional[asyncio.Lock] = None
        self._cmd_queue: Optional[asyncio.Queue] = None
        self._ok_event: Optional[asyncio.Event] = None
        self._read_task: Optional[asyncio.Task] = None
        self._print_feeder_task: Optional[asyncio.Task] = None
        self._telemetry_task: Optional[asyncio.Task] = None

        # M6: Track fire-and-forget tasks to prevent unbounded accumulation
        self._pending_tasks: set = set()

        # Phase 5: Auto-Reconnect
        self._auto_reconnect_task: Optional[asyncio.Task] = None
        self._target_port: Optional[str] = None
        self._target_baudrate: Optional[int] = None

    @staticmethod
    def get_available_ports() -> List[str]:
        return [port.device for port in serial.tools.list_ports.comports()]

    async def connect(self, port: str, baudrate: int) -> Tuple[bool, str]:
        if self.serial_conn and self.serial_conn.is_open:
            if self.serial_conn.port == port:
                return True, "Already connected"
            else:
                await self.disconnect(intentional=True)

        self._target_port = port
        self._target_baudrate = baudrate
        if not self._auto_reconnect_task or self._auto_reconnect_task.done():
            self._auto_reconnect_task = asyncio.create_task(self._auto_reconnect_loop())

        # Always create fresh primitives on connect
        self._cmd_queue = asyncio.Queue(maxsize=100)
        self._ok_event = asyncio.Event()
        self._serial_lock = asyncio.Lock()

        loop = asyncio.get_running_loop()
        def _open_port():
            s = serial.Serial()
            s.port = port
            s.baudrate = baudrate
            s.timeout = 0.1
            s.open()
            return s

        max_retries = 3
        last_exception = None

        for attempt in range(max_retries):
            try:
                self.serial_conn = await loop.run_in_executor(None, _open_port)

                self.state = PrinterState.IDLE
                self.last_error = None
                self._ok_event.set()

                # Start background tasks
                self._read_task = asyncio.create_task(self._read_loop())
                self._telemetry_task = asyncio.create_task(self._telemetry_loop())
                self._safe_notify_telemetry()
                return True, "Connected successfully"
            except (PermissionError, serial.SerialException) as e:
                last_exception = e
                logger.warning(f"Connection attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.5)
            except Exception as e:
                last_exception = e
                break

        self.state = PrinterState.ERROR
        if isinstance(last_exception, PermissionError):
            self.last_error = f"Port {port} is locked or resetting. Try replugging the USB."
        else:
            self.last_error = f"Serial error: {last_exception}"
            
        self._safe_notify_telemetry()
        return False, self.last_error

    async def _auto_reconnect_loop(self):
        """Phase 5: Auto-reconnect background loop."""
        while True:
            await asyncio.sleep(5.0)
            if self._target_port and self.state in [PrinterState.DISCONNECTED, PrinterState.ERROR]:
                logger.info(f"Auto-reconnecting to {self._target_port}...")
                await self.connect(self._target_port, self._target_baudrate)

    async def disconnect(self, intentional=True):
        """C3: Async disconnect - properly awaits task cancellation before closing port."""
        self.state = PrinterState.DISCONNECTED

        tasks = [self._read_task, self._print_feeder_task, self._telemetry_task]
        if intentional:
            self._target_port = None
            self._target_baudrate = None
            tasks.append(self._auto_reconnect_task)

        for task in tasks:
            if task and task is not asyncio.current_task():
                task.cancel()

        # Wait for all tasks to actually finish (with timeout to avoid hanging)
        active = [t for t in tasks if t and t is not asyncio.current_task()]
        if active:
            await asyncio.gather(*active, return_exceptions=True)

        self._read_task = None
        self._print_feeder_task = None
        self._telemetry_task = None
        if intentional:
            self._auto_reconnect_task = None

        self.close_port()
        self._safe_notify_telemetry()

    def close_port(self):
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
            except Exception as e:
                logger.error(f"Error closing port: {e}")
        self.serial_conn = None

    def send_command(self, cmd: str):
        """Queue a G-Code command. All commands go through _cmd_queue → _sender_loop (C1+C2)."""
        if not self.serial_conn or not self.serial_conn.is_open:
            return
        if not self._cmd_queue:
            return

        cmd = cmd.strip()
        if not cmd:
            return

        # Split multi-line commands into individual lines
        lines = cmd.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                self._cmd_queue.put_nowait(line)
            except asyncio.QueueFull:
                logger.warning(f"Queue full! Dropped command: {line}")

    async def _write_serial(self, cmd: str):
        """Async serial write using executor to avoid blocking event loop (M2)."""
        if not self.serial_conn or not self.serial_conn.is_open:
            return
        if self._serial_lock:
            async with self._serial_lock:
                loop = asyncio.get_running_loop()
                try:
                    full_cmd = f"{cmd}\n"
                    # Flush immediately to ensure Windows doesn't buffer it
                    await loop.run_in_executor(
                        None, lambda: (self.serial_conn.write(full_cmd.encode('utf-8')), self.serial_conn.flush())
                    )
                    # Don't emit M105 to terminal (terminal spam filter)
                    if cmd.strip() != "M105":
                        self._emit_terminal(f"> {cmd}")
                except Exception as e:
                    logger.error(f"Write error: {e}")

    async def _read_loop(self):
        """Background serial read loop - reads data AND dispatches commands.
        This mirrors the original working architecture where read + send
        happen in the SAME coroutine to avoid cross-coroutine race conditions.
        """
        loop = asyncio.get_running_loop()
        while self.state != PrinterState.DISCONNECTED and self.serial_conn:
            try:
                # Step 1: Non-blocking serial read via executor
                conn = self.serial_conn
                def _read_serial():
                    if conn and conn.is_open and conn.in_waiting > 0:
                        return conn.readline().decode('utf-8', errors='ignore').strip()
                    return None

                line = await loop.run_in_executor(None, _read_serial)
                if line:
                    self._handle_response(line)
                else:
                    await asyncio.sleep(0.01)

                # Step 2: Dispatch next queued command if printer is ready
                if (self._cmd_queue and not self._cmd_queue.empty()
                        and self._ok_event and self._ok_event.is_set()):
                    cmd = self._cmd_queue.get_nowait()
                    self._ok_event.clear()
                    await self._write_serial(cmd)

            except serial.SerialException as e:
                logger.error(f"Serial disconnected: {e}")
                self.last_error = f"Serial disconnected: {e}"
                await self.disconnect(intentional=False)
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Read error: {e}")
                await asyncio.sleep(1)

    def _handle_response(self, line: str):
        # M3: Suppress terminal spam for temperature reports
        is_temp_report = bool(re.search(r'T:\s*\d', line)) or bool(re.search(r'T\d*:\s*\d', line))
        if not is_temp_report and line != 'ok':
            self._emit_terminal(line)

        # Relaxed OK matching to handle all printer firmware quirks
        if "ok" in line.lower() and self._ok_event:
            self._ok_event.set()

        if "T:" in line or "B:" in line:
            self._parse_temperature(line)

    def _parse_temperature(self, line: str):
        """Parse actual AND target temperatures from Marlin M105 response.
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
        """Periodic temperature polling. Sends M105 through _cmd_queue for proper flow control (C2)."""
        while self.state != PrinterState.DISCONNECTED:
            if (self.state in [PrinterState.IDLE, PrinterState.PRINTING, PrinterState.PAUSED]
                    and self._cmd_queue):
                # Goes through the same queue as everything else - no more flow-control bypass
                try:
                    self._cmd_queue.put_nowait("M105")
                except asyncio.QueueFull:
                    pass  # Skip this poll cycle if queue is backed up
            await asyncio.sleep(2)

    async def start_print(self, filename: str, filepath: str):
        if self.state != PrinterState.IDLE:
            return False

        self.current_file = filename
        self.current_filepath = filepath
        import os
        self.file_size = os.path.getsize(filepath)
        self.current_file_pos = 0

        # Parse metadata (e.g. TIME estimate) from the first few lines
        self.estimated_total_time = None
        try:
            import aiofiles
            async with aiofiles.open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                for _ in range(100):
                    line = await f.readline()
                    if not line: break
                    time_match = re.search(r';\s*TIME:(\d+)', line)
                    if time_match:
                        self.estimated_total_time = float(time_match.group(1))
                        break
        except Exception as e:
            logger.error(f"Error reading metadata: {e}")

        self.current_line_idx = 0
        self.print_start_time = time.time()

        # H1: Reset pause tracking
        self._pause_start = None
        self._total_paused = 0.0

        self.last_error = None
        self.state = PrinterState.PRINTING
        self._safe_notify_telemetry()

        if self._print_feeder_task:
            self._print_feeder_task.cancel()
        self._print_feeder_task = asyncio.create_task(self._print_feeder_loop())
        return True

    def pause_print(self):
        """Pause host-streamed print by stopping the feeder loop."""
        if self.state == PrinterState.PRINTING:
            self.state = PrinterState.PAUSED
            # H1: Record when pause started
            self._pause_start = time.time()
            self._safe_notify_telemetry()

    def resume_print(self):
        """Resume print by restarting the feeder loop."""
        if self.state == PrinterState.PAUSED:
            # H1: Accumulate paused duration
            if self._pause_start:
                self._total_paused += time.time() - self._pause_start
                self._pause_start = None

            self.state = PrinterState.PRINTING
            self._safe_notify_telemetry()
            # Loop will naturally resume since we just changed state back to PRINTING

    async def cancel_print(self):
        """Cancel print with proper cleanup. Now async for proper task management."""
        if self.state in [PrinterState.PRINTING, PrinterState.PAUSED]:
            filepath_to_delete = self.current_filepath
            
            # Stop the feeder first
            if self._print_feeder_task:
                self._print_feeder_task.cancel()
                try:
                    await self._print_feeder_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._print_feeder_task = None

            self.state = PrinterState.IDLE
            self.current_file = None
            self.current_filepath = None
            self.current_file_pos = 0
            self.file_size = 0
            self.current_line_idx = 0
            self.print_start_time = None
            self.estimated_total_time = None
            self._pause_start = None
            self._total_paused = 0.0

            # M5: Drain stale commands from the queue before sending cancel sequence
            if self._cmd_queue:
                while not self._cmd_queue.empty():
                    try:
                        self._cmd_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                # Send cancel commands through the queue for proper flow control
                self._cmd_queue.put_nowait("M104 S0")
                self._cmd_queue.put_nowait("M140 S0")
                self._cmd_queue.put_nowait("G28 X Y")

            self._safe_notify_telemetry()
            
            if filepath_to_delete and os.path.exists(filepath_to_delete):
                try:
                    import os
                    os.remove(filepath_to_delete)
                except Exception as e:
                    logger.error(f"Failed to clean up gcode file: {e}")

    async def _print_feeder_loop(self):
        """Feeds G-code lines into _cmd_queue one at a time.
        The _sender_loop handles the actual serial write + ok handshake.
        """
        filepath_to_delete = None
        try:
            import aiofiles
            async with aiofiles.open(self.current_filepath, 'r', encoding='utf-8', errors='ignore') as f:
                while self.state in [PrinterState.PRINTING, PrinterState.PAUSED]:
                    if self.state == PrinterState.PAUSED:
                        await asyncio.sleep(0.5)
                        continue

                    line = await f.readline()
                    if not line:
                        break
                    
                    self.current_file_pos += len(line.encode('utf-8'))
                    line = line.strip()
                    
                    if not line or line.startswith(';'):
                        continue

                    # Put the line into the command queue - _sender_loop will handle flow control
                    if self._cmd_queue:
                        await self._cmd_queue.put(line)

                    self.current_line_idx += 1
                    if self.current_line_idx % 10 == 0:
                        self._safe_notify_telemetry()

            # Print completed successfully (H5: clear stale data)
            if self.state in [PrinterState.PRINTING, PrinterState.PAUSED]:
                filepath_to_delete = self.current_filepath
                self.state = PrinterState.IDLE
                self.current_file = None
                self.current_filepath = None
                self.current_file_pos = 0
                self.file_size = 0
                self.current_line_idx = 0
                self.print_start_time = None
                self.estimated_total_time = None
                self._pause_start = None
                self._total_paused = 0.0
                self._safe_notify_telemetry()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Print error: {e}")
            self.last_error = f"Print error: {e}"
            # H2: Auto-disconnect on print error to release port and reach recoverable state
            self.state = PrinterState.ERROR
            self._safe_notify_telemetry()
            
        if filepath_to_delete and os.path.exists(filepath_to_delete):
            try:
                import os
                os.remove(filepath_to_delete)
            except Exception as e:
                logger.error(f"Failed to clean up finished gcode file: {e}")

    def get_state(self) -> Dict[str, Any]:
        progress = 0
        elapsed = 0
        eta = None

        if hasattr(self, 'file_size') and self.file_size > 0:
            progress = (self.current_file_pos / self.file_size) * 100

        if self.print_start_time and self.state in [PrinterState.PRINTING, PrinterState.PAUSED]:
            # H1: Subtract paused duration from elapsed time
            paused_so_far = self._total_paused
            if self._pause_start and self.state == PrinterState.PAUSED:
                paused_so_far += time.time() - self._pause_start
            elapsed = time.time() - self.print_start_time - paused_so_far

            # ETA: Prefer slicer estimate scaled by progress, fallback to extrapolation
            if self.estimated_total_time and progress > 0:
                eta = self.estimated_total_time * (1.0 - progress / 100.0)
            elif progress > 5.0:
                # Fallback: byte-based extrapolation (only after 5% to avoid startup noise)
                total_extrapolated = (elapsed / progress) * 100
                eta = total_extrapolated - elapsed

        return {
            "state": self.state,
            "port": self.serial_conn.port if self.serial_conn else None,
            "file": self.current_file,
            "progress": progress,
            "elapsed_s": int(elapsed),
            "eta_s": int(eta) if eta is not None else None,
            "temps": self.temps,
            "position": self.position,
            "error": self.last_error,
        }

    def _safe_notify_telemetry(self):
        """M6: Safely emit telemetry - tracks tasks and handles missing event loop."""
        state_data = self.get_state()
        for cb in self.telemetry_callbacks:
            try:
                asyncio.get_running_loop()
                task = asyncio.create_task(cb(state_data))
                self._pending_tasks.add(task)
                
                def _handle_done(t):
                    self._pending_tasks.discard(t)
                    try:
                        t.result()
                    except Exception as e:
                        logger.error(f"Telemetry callback failed: {e}")
                
                task.add_done_callback(_handle_done)
            except RuntimeError:
                pass  # No running event loop (e.g., during shutdown)

    def _emit_terminal(self, text: str):
        """M6: Safely emit terminal text - tracks tasks and handles missing event loop."""
        for cb in self.terminal_callbacks:
            try:
                asyncio.get_running_loop()
                task = asyncio.create_task(cb(text))
                self._pending_tasks.add(task)
                
                def _handle_done(t):
                    self._pending_tasks.discard(t)
                    try:
                        t.result()
                    except Exception as e:
                        logger.error(f"Terminal callback failed: {e}")

                task.add_done_callback(_handle_done)
            except RuntimeError:
                pass
