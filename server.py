import os
import sys
import json
import stat
import socket
import select
import shutil
import zipfile
import platform
import threading
import subprocess
import urllib.request
from pathlib import Path

XRAY_VERSION = "v26.7.28"
BASE_DIR = Path(__file__).resolve().parent
XRAY_BIN = BASE_DIR / "xray"
XRAY_CONFIG = BASE_DIR / "config.json"
HTML_FILE = BASE_DIR / "index.html"

XRAY_INTERNAL_PORT = 10000
LISTEN_HOST = "0.0.0.0"

def get_env_port():
    for key in ("PORT", "SERVER_PORT", "APP_PORT"):
        val = os.environ.get(key)
        if val and val.isdigit():
            return int(val)
    return 8080

LISTEN_PORT = get_env_port()
WS_PATH = os.environ.get("WS_PATH", "/api/v2/stream").strip()
if not WS_PATH.startswith("/"):
    WS_PATH = "/" + WS_PATH

VLESS_UUID = os.environ.get("VLESS_UUID", "13cac18e-685e-4cc9-ae6b-e9bbfd897c49").strip()

def download_xray():
    if XRAY_BIN.exists():
        return
    arch = platform.machine().lower()
    if arch in ("x86_64", "amd64"):
        asset = "Xray-linux-64.zip"
    elif arch in ("aarch64", "arm64"):
        asset = "Xray-linux-arm64-v8a.zip"
    else:
        raise RuntimeError(f"Unsupported architecture: {arch}")

    url = f"https://github.com/XTLS/Xray-core/releases/download/{XRAY_VERSION}/{asset}"
    zip_path = BASE_DIR / "xray.zip"
    print(f"[+] Downloading Xray {XRAY_VERSION} ({asset})...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(zip_path, "wb") as f:
        shutil.copyfileobj(resp, f)

    with zipfile.ZipFile(zip_path) as zf:
        zf.extract("xray", BASE_DIR)
    zip_path.unlink(missing_ok=True)
    XRAY_BIN.chmod(XRAY_BIN.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print("[+] Xray downloaded and marked executable.")

def generate_config():
    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": XRAY_INTERNAL_PORT,
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
    XRAY_CONFIG.write_text(json.dumps(config, indent=2))

def run_xray():
    download_xray()
    generate_config()
    print(f"[+] Launching Xray on 127.0.0.1:{XRAY_INTERNAL_PORT} with path: {WS_PATH}")
    subprocess.Popen([str(XRAY_BIN), "run", "-config", str(XRAY_CONFIG)])

def forward_sockets(src, dst):
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

def load_landing_html():
    if HTML_FILE.exists():
        return HTML_FILE.read_bytes()
    return b"<!DOCTYPE html><html><body><h1>API Gateway Active</h1></body></html>"

def handle_client(client_sock):
    try:
        client_sock.settimeout(10.0)
        initial_chunk = client_sock.recv(4096)
        if not initial_chunk:
            client_sock.close()
            return
        client_sock.settimeout(None)

        first_line = initial_chunk.split(b"\r\n", 1)[0].decode("latin-1", errors="ignore")
        parts = first_line.split(" ")
        method = parts[0] if len(parts) > 0 else ""
        request_path = parts[1] if len(parts) > 1 else "/"

        headers_lower = initial_chunk.lower()
        is_websocket = b"upgrade: websocket" in headers_lower

        # 1. Forward designated WebSocket stream to internal Xray
        if is_websocket and request_path.startswith(WS_PATH):
            xray_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            xray_sock.connect(("127.0.0.1", XRAY_INTERNAL_PORT))
            xray_sock.sendall(initial_chunk)
            threading.Thread(target=forward_sockets, args=(client_sock, xray_sock), daemon=True).start()
            return

        # 2. Platform Health Check endpoint
        if request_path == "/health":
            resp = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{\"status\":\"ok\"}"
            client_sock.sendall(resp)
            client_sock.close()
            return

        # 3. Legitimate HTML Camouflage for root / other paths
        html_content = load_landing_html()
        header = (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(html_content)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("utf-8")
        client_sock.sendall(header + html_content)
        client_sock.close()
    except Exception:
        client_sock.close()

def main():
    threading.Thread(target=run_xray, daemon=True).start()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_HOST, LISTEN_PORT))
    server.listen(128)
    print(f"[+] Web camouflage server running on {LISTEN_HOST}:{LISTEN_PORT}")
    
    while True:
        try:
            client_sock, _ = server.accept()
            threading.Thread(target=handle_client, args=(client_sock,), daemon=True).start()
        except KeyboardInterrupt:
            break
        except Exception:
            pass

if __name__ == "__main__":
    main()
