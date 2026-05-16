from enum import Enum
from pydantic import BaseModel

class PrinterState(str, Enum):
    DISCONNECTED = "Disconnected"
    IDLE = "Idle"
    PRINTING = "Printing"
    PAUSED = "Paused"
    ERROR = "Error"

class ConnectRequest(BaseModel):
    port: str
    baudrate: int

class CommandRequest(BaseModel):
    command: str
