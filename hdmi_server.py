#!/usr/bin/env python3
import os
import glob
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler

# Výchozí port
PORT = 1111

def find_hdmi_status_path():
    """Najde cestu k DRM statusu pro HDMI výstup v systému."""
    default_path = "/sys/class/drm/card0-HDMI-A-1/status"
    if os.path.exists(default_path):
        return default_path
    
    # Hledání alternativních HDMI výstupů (např. card1 nebo HDMI-A-2)
    matches = glob.glob("/sys/class/drm/*HDMI*/status")
    if matches:
        return matches[0]
    
    return default_path

HDMI_STATUS_PATH = find_hdmi_status_path()

def set_hdmi(enable: bool) -> bool:
    """Zapne nebo vypne HDMI zápisem do DRM sysfs."""
    state_str = "on" if enable else "off"
    try:
        with open(HDMI_STATUS_PATH, "w") as f:
            f.write(state_str + "\n")
        print(f"[HDMI] Monitor úspěšně přepnut do stavu: {state_str.upper()} ({HDMI_STATUS_PATH})")
        return True
    except PermissionError:
        print(f"[CHYBA] Nedostatečná práva pro zápis do {HDMI_STATUS_PATH}. Spusťte jako root/sudo.")
    except Exception as e:
        print(f"[CHYBA] Zápis do {HDMI_STATUS_PATH} selhal: {e}")
    return False

def get_hdmi_status() -> str:
    """Přečte aktuální stav HDMI konektoru."""
    try:
        with open(HDMI_STATUS_PATH, "r") as f:
            return f.read().strip()
    except Exception:
        return "neznámý"

class HDMIRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/hdmi/on":
            set_hdmi(True)
            self._redirect_home()
        elif self.path == "/hdmi/off":
            set_hdmi(False)
            self._redirect_home()
        elif self.path == "/" or self.path.startswith("/?"):
            self._serve_dashboard()
        else:
            self.send_error(404, "Stránka nenalezena")

    def _redirect_home(self):
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def _serve_dashboard(self):
        status = get_hdmi_status()
        is_on = status.lower() in ["connected", "on", "enabled"]

        status_text = "ZAPNUTO" if is_on else "VYPNUTO / SPÁNEK"
        status_color = "#22c55e" if is_on else "#ef4444"

        html = f"""<!DOCTYPE html>
<html lang="cs">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HDMI Server</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background-color: #0f172a;
            color: #f8fafc;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            padding: 16px;
            box-sizing: border-box;
        }}
        .card {{
            background: #1e293b;
            padding: 32px;
            border-radius: 16px;
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.4);
            text-align: center;
            width: 100%;
            max-width: 400px;
        }}
        h1 {{
            font-size: 1.5rem;
            margin-bottom: 24px;
            color: #94a3b8;
        }}
        .status-box {{
            padding: 12px;
            border-radius: 8px;
            background: #0f172a;
            font-weight: bold;
            font-size: 1.1rem;
            margin-bottom: 32px;
            color: {status_color};
            border: 1px solid {status_color};
        }}
        .btn {{
            display: block;
            width: 100%;
            padding: 16px;
            margin: 12px 0;
            border: none;
            border-radius: 10px;
            font-size: 1.1rem;
            font-weight: 600;
            text-decoration: none;
            cursor: pointer;
            transition: opacity 0.2s;
            box-sizing: border-box;
        }}
        .btn:hover {{ opacity: 0.9; }}
        .btn-on {{ background-color: #22c55e; color: white; }}
        .btn-off {{ background-color: #ef4444; color: white; }}
        .footer {{
            margin-top: 24px;
            font-size: 0.8rem;
            color: #64748b;
        }}
    </style>
</head>
<body>
    <div class="card">
        <h1>HDMI Control Server</h1>
        <div class="status-box">Stav: {status_text}</div>
        <a href="/hdmi/on" class="btn btn-on">Zapnout HDMI (ON)</a>
        <a href="/hdmi/off" class="btn btn-off">Vypnout HDMI (OFF)</a>
        <div class="footer">Cesta: {HDMI_STATUS_PATH}</div>
    </div>
</body>
</html>
"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        # Čisté logování požadavků
        print(f"[HTTP] {self.address_string()} - {format % args}")

def run_server(port):
    # ==============================================================
    # ⚡ AUTOMATICKÉ VYPNUTÍ MONITORU PŘI SPUŠTĚNÍ PROGRAMU ⚡
    # ==============================================================
    print("[STARTUP] Automatické vypínání HDMI monitoru při startu...")
    set_hdmi(False)

    server_address = ("", port)
    httpd = HTTPServer(server_address, HDMIRequestHandler)
    print(f"[SERVER] Běží na http://0.0.0.0:{port} (HDMI cesta: {HDMI_STATUS_PATH})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[SERVER] Zastavování...")
        httpd.server_close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HDMI Web Control Server")
    parser.add_argument("--port", type=int, default=PORT, help=f"Port serveru (výchozí: {PORT})")
    args = parser.parse_args()

    run_server(args.port)
