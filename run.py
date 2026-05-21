import os
import socket
import sys
import uvicorn

def find_open_port(start_port: int, host: str = "0.0.0.0") -> int:
    port = start_port
    while True:
        in_use = False
        
        # 1. Try to connect to 127.0.0.1
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.1)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    in_use = True
        except Exception:
            pass
            
        # 2. Try to bind to 127.0.0.1
        if not in_use:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("127.0.0.1", port))
            except OSError:
                in_use = True

        # 3. Try to bind to host (0.0.0.0)
        if not in_use:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind((host, port))
            except OSError:
                in_use = True

        if not in_use:
            return port
            
        port += 1
        if port > 65535:
            raise RuntimeError("No open ports found!")

if __name__ == "__main__":
    start_port = 8000
    try:
        port = find_open_port(start_port)
        if port != start_port:
            print(f"[*] Port {start_port} is in use. Falling back to the next available port: {port}")
        
        os.environ["PORT"] = str(port)
        uvicorn.run("app.main:app", host="0.0.0.0", port=port)
    except Exception as e:
        print(f"[-] Error starting server: {e}", file=sys.stderr)
        sys.exit(1)
