import os
import sys
import json
import socket
import select
import threading
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "index.html"
CONFIG_FILE = Path("/tmp/xray-config.json")
XRAY_BIN = "/usr/local/bin/xray"

XRAY_PORT = 10000
LISTEN_HOST = "0.0.0.0"

# Read Deplexo port injection
PORT = int(os.environ.get("PORT", "3000"))
WS_PATH = os.environ.get("WS_PATH", "/api/v1/live").strip()
if not WS_PATH.startswith("/"):
    WS_PATH = "/" + WS_PATH

VLESS_UUID = os.environ.get("VLESS_UUID", "13cac18e-685e-4cc9-ae6b-e9bbfd897c49").strip()

def generate_config():
    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": XRAY_PORT,
                "protocol": "vless",
                "settings": {
                    "clients": [{"id": VLESS_UUID}],
                    "decryption": "none"
                },
                "streamSettings": {
                    "network": "ws",
                    "security": "none",
                    "wsSettings": {"path": WS_PATH}
                }
            }
        ],
        "outbounds": [{"protocol": "freedom", "tag": "direct"}]
    }
    CONFIG_FILE.write_text(json.dumps(config, indent=2))

def start_xray():
    generate_config()
    print(f"[+] Starting Xray core on 127.0.0.1:{XRAY_PORT}...")
    subprocess.Popen([XRAY_BIN, "run", "-config", str(CONFIG_FILE)])

def forward_stream(src, dst):
    try:
        while True:
            r, _, _ = select.select([src, dst], [], [], 60)
            if not r:
                break
            for s in r:
                data = s.recv(32768)
                if not data:
                    return
                target = dst if s is src else src
                target.sendall(data)
    except Exception:
        pass
    finally:
        src.close()
        dst.close()

def load_landing():
    if HTML_FILE.exists():
        return HTML_FILE.read_bytes()
    return b"<!DOCTYPE html><html><body><h1>Service Online</h1></body></html>"

def handle_request(client_sock):
    try:
        client_sock.settimeout(5.0)
        chunk = client_sock.recv(4096)
        if not chunk:
            client_sock.close()
            return
        client_sock.settimeout(None)

        first_line = chunk.split(b"\r\n", 1)[0].decode("latin-1", errors="ignore")
        parts = first_line.split(" ")
        request_path = parts[1] if len(parts) > 1 else "/"
        is_ws = b"upgrade: websocket" in chunk.lower()

        # Route WebSocket tunnel to internal Xray
        if is_ws and request_path.startswith(WS_PATH):
            xray_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            xray_sock.connect(("127.0.0.1", XRAY_PORT))
            xray_sock.sendall(chunk)
            threading.Thread(target=forward_stream, args=(client_sock, xray_sock), daemon=True).start()
            return

        # Platform Health Probes
        if request_path in ("/health", "/ping"):
            res = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\nOK"
            client_sock.sendall(res)
            client_sock.close()
            return

        # Serve Camouflage Landing Page
        html = load_landing()
        headers = (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(html)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("utf-8")
        client_sock.sendall(headers + html)
        client_sock.close()
    except Exception:
        client_sock.close()

def main():
    threading.Thread(target=start_xray, daemon=True).start()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_HOST, PORT))
    srv.listen(128)
    print(f"[+] Deplexo front listener active on {LISTEN_HOST}:{PORT}")

    while True:
        try:
            sock, _ = srv.accept()
            threading.Thread(target=handle_request, args=(sock,), daemon=True).start()
        except KeyboardInterrupt:
            break
        except Exception:
            pass

if __name__ == "__main__":
    main()
