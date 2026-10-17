#!/usr/bin/env python3
import os
import glob
import argparse
import subprocess
from urllib.parse import urlparse, parse_qs
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

PORT = 1111

# --- OVLÁDÁNÍ HDMI ---
def find_hdmi_status_path():
    default_path = "/sys/class/drm/card0-HDMI-A-1/status"
    if os.path.exists(default_path):
        return default_path
    matches = glob.glob("/sys/class/drm/*HDMI*/status")
    return matches[0] if matches else default_path

HDMI_STATUS_PATH = find_hdmi_status_path()

def set_hdmi(enable: bool) -> bool:
    state_str = "on" if enable else "off"
    try:
        with open(HDMI_STATUS_PATH, "w") as f:
            f.write(state_str + "\n")
        print(f"[HDMI] Monitor přepnut do stavu: {state_str.upper()}")
        return True
    except Exception as e:
        print(f"[CHYBA] Zápis do {HDMI_STATUS_PATH} selhal: {e}")
        return False

def get_hdmi_status() -> str:
    try:
        with open(HDMI_STATUS_PATH, "r") as f:
            return f.read().strip()
    except Exception:
        return "neznámý"

# --- OVLÁDÁNÍ MYŠI (EVDEV / XDOTOOL) ---
try:
    import evdev
    from evdev import UInput, ecodes as e
    HAS_EVDEV = True
except ImportError:
    HAS_EVDEV = False

class MouseController:
    def __init__(self):
        self.ui = None
        if HAS_EVDEV:
            try:
                cap = {
                    e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL],
                    e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE]
                }
                self.ui = UInput(cap, name="RPi-Web-Touchpad")
                print("[MOUSE] Úspěšně inicializován uinput/evdev ovladač myši.")
            except Exception as ex:
                print(f"[MOUSE] evdev inicializace selhala ({ex}), přepínám na xdotool fallback.")

    def move(self, dx: int, dy: int):
        if self.ui:
            self.ui.write(e.EV_REL, e.REL_X, dx)
            self.ui.write(e.EV_REL, e.REL_Y, dy)
            self.ui.syn()
        else:
            subprocess.run(
                ["xdotool", "mousemove_relative", "--", str(dx), str(dy)],
                env=dict(os.environ, DISPLAY=":0"),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

    def click(self, button: str = "left"):
        if self.ui:
            btn = e.BTN_LEFT if button == "left" else e.BTN_RIGHT
            self.ui.write(e.EV_KEY, btn, 1)
            self.ui.syn()
            self.ui.write(e.EV_KEY, btn, 0)
            self.ui.syn()
        else:
            btn_num = "1" if button == "left" else "3"
            subprocess.run(
                ["xdotool", "click", btn_num],
                env=dict(os.environ, DISPLAY=":0"),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

mouse = MouseController()

# --- HTTP HANDLER ---
class ServerHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._serve_dashboard()
        elif path == "/hdmi/on":
            set_hdmi(True)
            self._redirect_home()
        elif path == "/hdmi/off":
            set_hdmi(False)
            self._redirect_home()
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/api/mouse/move":
            try:
                dx = int(params.get("dx", [0])[0])
                dy = int(params.get("dy", [0])[0])
                mouse.move(dx, dy)
                self._send_status(204)
            except Exception:
                self._send_status(400)

        elif path == "/api/mouse/click":
            btn = params.get("btn", ["left"])[0]
            mouse.click(btn)
            self._send_status(204)

        elif path == "/hdmi/on":
            set_hdmi(True)
            self._send_status(200)

        elif path == "/hdmi/off":
            set_hdmi(False)
            self._send_status(200)

        else:
            self.send_error(404)

    def _send_status(self, code):
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _redirect_home(self):
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def _serve_dashboard(self):
        status = get_hdmi_status()
        is_on = status.lower() in ["connected", "on", "enabled"]
        status_text = "ZAPNUTO" if is_on else "VYPNUTO"
        status_color = "#22c55e" if is_on else "#ef4444"

        html = f"""<!DOCTYPE html>
<html lang="cs">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>RPi Remote Control</title>
    <style>
        * {{
            box-sizing: border-box;
            user-select: none;
            -webkit-user-select: none;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background-color: #0f172a;
            color: #f8fafc;
            margin: 0;
            padding: 12px;
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
            gap: 8px;
        }}
        .status-badge {{
            padding: 8px 12px;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: bold;
            color: {status_color};
            border: 1px solid {status_color};
            white-space: nowrap;
        }}
        .hdmi-buttons {{
            display: flex;
            gap: 8px;
            flex-grow: 1;
            justify-content: flex-end;
        }}
        .btn-hdmi {{
            padding: 8px 14px;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: bold;
            border: none;
            cursor: pointer;
            color: white;
            text-decoration: none;
        }}
        .btn-on {{ background-color: #16a34a; }}
        .btn-off {{ background-color: #dc2626; }}

        #touchpad {{
            flex: 1;
            background-color: #1e293b;
            border-radius: 16px;
            border: 2px dashed #475569;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-direction: column;
            color: #94a3b8;
            touch-action: none;
            box-shadow: inset 0 2px 8px rgba(0,0,0,0.4);
        }}
        #touchpad.active {{
            border-color: #38bdf8;
            background-color: #1a2537;
        }}
        .touchpad-label {{
            font-size: 1.1rem;
            font-weight: 500;
            pointer-events: none;
        }}
        .touchpad-hint {{
            font-size: 0.8rem;
            color: #64748b;
            margin-top: 6px;
            pointer-events: none;
        }}
        .mouse-buttons {{
            display: flex;
            gap: 12px;
            margin-top: 12px;
            height: 65px;
        }}
        .btn-mouse {{
            flex: 1;
            background: #334155;
            color: white;
            border: none;
            border-radius: 12px;
            font-size: 1rem;
            font-weight: 600;
            touch-action: manipulation;
            cursor: pointer;
        }}
        .btn-mouse:active {{
            background: #64748b;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="status-badge">HDMI: {status_text}</div>
        <div class="hdmi-buttons">
            <a href="/hdmi/on" class="btn-hdmi btn-on">Zapnout</a>
            <a href="/hdmi/off" class="btn-hdmi btn-off">Vypnout</a>
        </div>
    </div>

    <div id="touchpad">
        <div class="touchpad-label">🖱️ Dotyková plocha</div>
        <div class="touchpad-hint">Tažením hýbete myší • Klepnutí = Levý klik</div>
    </div>

    <div class="mouse-buttons">
        <button class="btn-mouse" id="btn-left">Levé tl.</button>
        <button class="btn-mouse" id="btn-right">Pravé tl.</button>
    </div>

    <script>
        const pad = document.getElementById("touchpad");
        const SENSITIVITY = 1.6; // Citlivost myši

        let lastX = 0, lastY = 0;
        let startX = 0, startY = 0;
        let startTime = 0;
        let accX = 0, accY = 0;
        let isSending = false;

        // Odesílání pohybu plynule ve smyčce (cca 40 FPS)
        function flushMove() {
            if ((accX !== 0 || accY !== 0) && !isSending) {
                const x = accX;
                const y = accY;
                accX = 0;
                accY = 0;
                isSending = true;
                fetch(`/api/mouse/move?dx=${{x}}&dy=${{y}}`, {{ method: 'POST' }})
                    .finally(() => {{ isSending = false; }});
            }
        }
        setInterval(flushMove, 25);

        pad.addEventListener("touchstart", (e) => {{
            e.preventDefault();
            pad.classList.add("active");
            const t = e.touches[0];
            lastX = t.clientX;
            lastY = t.clientY;
            startX = t.clientX;
            startY = t.clientY;
            startTime = Date.now();
        }}, {{ passive: false }});

        pad.addEventListener("touchmove", (e) => {{
            e.preventDefault();
            const t = e.touches[0];
            const dx = (t.clientX - lastX) * SENSITIVITY;
            const dy = (t.clientY - lastY) * SENSITIVITY;
            
            accX += Math.round(dx);
            accY += Math.round(dy);

            lastX = t.clientX;
            lastY = t.clientY;
        }}, {{ passive: false }});

        pad.addEventListener("touchend", (e) => {{
            e.preventDefault();
            pad.classList.remove("active");
            const dist = Math.hypot(lastX - startX, lastY - startY);
            const duration = Date.now() - startTime;

            // Rychlé klepnutí na ploše bez posunu provede klik
            if (dist < 8 && duration < 250) {{
                sendClick('left');
            }}
        }}, {{ passive: false }});

        function sendClick(btn) {{
            fetch(`/api/mouse/click?btn=${{btn}}`, {{ method: 'POST' }});
        }}

        document.getElementById("btn-left").addEventListener("click", () => sendClick('left'));
        document.getElementById("btn-right").addEventListener("click", () => sendClick('right'));
    </script>
</body>
</html>
"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        # Filtrovat logy myši, aby nezaplavovaly konzoli/journalctl
        if "api/mouse" not in args[0]:
            print(f"[HTTP] {self.address_string()} - {format % args}")

def run_server(port):
    print("[STARTUP] Automatické vypínání HDMI monitoru...")
    set_hdmi(False)

    server_address = ("", port)
    httpd = ThreadingHTTPServer(server_address, ServerHandler)
    print(f"[SERVER] Běží na portu {port} (Touchpad + HDMI)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[SERVER] Zastaven.")
        httpd.server_close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    run_server(args.port)
