#!/usr/bin/env python3
import os
import io
import re
import glob
import time
import argparse
import threading
import subprocess
from urllib.parse import urlparse, parse_qs
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

PORT = 1111

# --- PROSTŘEDÍ PRO WAYLAND I X11 ---
def get_display_env():
    """Nastaví prostředí pro běh grafických nástrojů z background služby."""
    env = os.environ.copy()
    env["DISPLAY"] = ":0"
    
    # Detekce Wayland socketu a runtime adresáře
    uid = os.getuid()
    runtime_dir = f"/run/user/{uid}" if os.path.exists(f"/run/user/{uid}") else "/run/user/1000"
    env["XDG_RUNTIME_DIR"] = runtime_dir
    
    for wl in ["wayland-1", "wayland-0"]:
        if os.path.exists(os.path.join(runtime_dir, wl)):
            env["WAYLAND_DISPLAY"] = wl
            break

    # X11 autorizace
    for path in ["/home/cernytom/.Xauthority", "/home/pi/.Xauthority", os.path.expanduser("~/.Xauthority")]:
        if os.path.exists(path):
            env["XAUTHORITY"] = path
            break
    return env

# --- OVLÁDÁNÍ ZVUKU ---
def refresh_audio():
    """Znovu inicializuje a probudí zvukové rozhraní HDMI."""
    print("[AUDIO] Obnovuji zvukové subsystémy...")
    commands = [
        "udevadm trigger --subsystem-match=sound",
        "alsactl init",
        "alsactl restore",
        "pkill -x wireplumber",
        "pkill -x pipewire",
        "pulseaudio -k"
    ]
    for cmd in commands:
        try:
            subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    try:
        cmd_sink = "pactl list sinks short | grep -i hdmi | head -n 1 | awk '{print $2}'"
        res = subprocess.run(cmd_sink, shell=True, capture_output=True, text=True)
        hdmi_sink = res.stdout.strip()
        if hdmi_sink:
            subprocess.run(f"pactl set-default-sink {hdmi_sink}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    print("[AUDIO] Zvuk obnoven.")

def delayed_audio_refresh():
    time.sleep(1.5)
    refresh_audio()

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
        print(f"[HDMI] Monitor přepnut do: {state_str.upper()}")
        if enable:
            threading.Thread(target=delayed_audio_refresh, daemon=True).start()
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
                print("[MOUSE] Inicializován uinput/evdev ovladač myši.")
            except Exception:
                pass

    def move(self, dx: int, dy: int):
        if self.ui:
            self.ui.write(e.EV_REL, e.REL_X, dx)
            self.ui.write(e.EV_REL, e.REL_Y, dy)
            self.ui.syn()
        else:
            subprocess.run(
                ["xdotool", "mousemove_relative", "--", str(dx), str(dy)],
                env=get_display_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
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
                env=get_display_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

mouse = MouseController()

# --- SNÍMÁNÍ OBRAZOVKY A CENTROVÁNÍ NA MYŠ ---
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

last_capture_time = 0
cached_image_bytes = None
CROP_WIDTH = 500
CROP_HEIGHT = 400

def get_mouse_position():
    """Získá aktuální pozici myši pomocí xdotool."""
    try:
        res = subprocess.run(
            ["xdotool", "getmouselocation"],
            env=get_display_env(), capture_output=True, text=True, timeout=1
        )
        match = re.search(r"x:(\d+)\s+y:(\d+)", res.stdout)
        if match:
            return int(match.group(1)), int(match.group(2))
    except Exception:
        pass
    return None, None

def capture_centered_crop():
    """Pořídí snímek (přes grim pro Wayland nebo scrot pro X11) a ořízne na myš."""
    global last_capture_time, cached_image_bytes

    now = time.time()
    # Cache 250ms pro optimalizaci výkonu RPi 3
    if cached_image_bytes and (now - last_capture_time < 0.25):
        return cached_image_bytes

    if not HAS_PIL:
        return None

    temp_raw = "/dev/shm/rpi_raw_screen.jpg"
    env = get_display_env()

    # Smazat starý snímek, aby nezůstal viset
    if os.path.exists(temp_raw):
        try:
            os.remove(temp_raw)
        except OSError:
            pass

    try:
        # 1. Pokus: grim (pro Wayland na Raspberry Pi OS Bookworm)
        ret = subprocess.run(
            ["grim", "-q", "50", temp_raw],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2
        )

        # 2. Pokus: pokud grim selhal nebo není nainstalován, použijeme scrot (pro X11)
        if ret.returncode != 0 or not os.path.exists(temp_raw):
            subprocess.run(
                ["scrot", "-z", "-o", "-q", "50", temp_raw],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2
            )

        if not os.path.exists(temp_raw):
            return None

        mx, my = get_mouse_position()
        img = Image.open(temp_raw)
        img_w, img_h = img.size

        # Pokud xdotool nenašel pozici, vycentrujeme na střed obrazovky
        if mx is None or my is None:
            mx, my = img_w // 2, img_h // 2

        canvas = Image.new("RGB", (CROP_WIDTH, CROP_HEIGHT), (20, 24, 33))

        crop_x1 = mx - CROP_WIDTH // 2
        crop_y1 = my - CROP_HEIGHT // 2
        crop_x2 = crop_x1 + CROP_WIDTH
        crop_y2 = crop_y1 + CROP_HEIGHT

        src_x1 = max(0, crop_x1)
        src_y1 = max(0, crop_y1)
        src_x2 = min(img_w, crop_x2)
        src_y2 = min(img_h, crop_y2)

        if src_x2 > src_x1 and src_y2 > src_y1:
            cropped = img.crop((src_x1, src_y1, src_x2, src_y2))
            paste_x = src_x1 - crop_x1
            paste_y = src_y1 - crop_y1
            canvas.paste(cropped, (paste_x, paste_y))

        buf = io.BytesIO()
        canvas.save(buf, format="JPEG", quality=55, optimize=True)
        cached_image_bytes = buf.getvalue()
        last_capture_time = now
        return cached_image_bytes

    except Exception as ex:
        print(f"[SCREEN] Chyba: {ex}")
        return None

# --- HTML ŠABLONA ---
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="cs">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>RPi Remote Touchpad</title>
    <style>
        * {
            box-sizing: border-box;
            user-select: none;
            -webkit-user-select: none;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background-color: #0f172a;
            color: #f8fafc;
            margin: 0;
            padding: 8px;
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }
        .header {
            display: flex;
            flex-direction: column;
            gap: 6px;
            margin-bottom: 8px;
        }
        .header-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .status-badge {
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 0.8rem;
            font-weight: bold;
            color: __STATUS_COLOR__;
            border: 1px solid __STATUS_COLOR__;
        }
        .action-buttons {
            display: flex;
            gap: 6px;
            width: 100%;
        }
        .btn-action {
            flex: 1;
            padding: 8px 4px;
            border-radius: 8px;
            font-size: 0.8rem;
            font-weight: bold;
            border: none;
            cursor: pointer;
            color: white;
            text-align: center;
            text-decoration: none;
        }
        .btn-on { background-color: #16a34a; }
        .btn-off { background-color: #dc2626; }
        .btn-audio { background-color: #2563eb; }
        .btn-stream { background-color: #7c3aed; }

        #touchpad {
            flex: 1;
            position: relative;
            background-color: #1e293b;
            border-radius: 16px;
            border: 2px solid #475569;
            overflow: hidden;
            touch-action: none;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        #touchpad.active {
            border-color: #38bdf8;
        }
        #screen-view {
            width: 100%;
            height: 100%;
            object-fit: cover;
            position: absolute;
            top: 0;
            left: 0;
            pointer-events: none;
            opacity: 0.9;
        }
        .crosshair {
            position: absolute;
            width: 24px;
            height: 24px;
            pointer-events: none;
            z-index: 10;
        }
        .crosshair::before {
            content: '';
            position: absolute;
            top: 11px;
            left: 0;
            width: 24px;
            height: 2px;
            background: #ef4444;
            box-shadow: 0 0 4px #000;
        }
        .crosshair::after {
            content: '';
            position: absolute;
            top: 0;
            left: 11px;
            width: 2px;
            height: 24px;
            background: #ef4444;
            box-shadow: 0 0 4px #000;
        }
        .touchpad-hint {
            position: absolute;
            bottom: 8px;
            font-size: 0.75rem;
            background: rgba(15, 23, 42, 0.75);
            padding: 4px 10px;
            border-radius: 12px;
            color: #cbd5e1;
            pointer-events: none;
            z-index: 10;
        }
        .mouse-buttons {
            display: flex;
            gap: 10px;
            margin-top: 8px;
            height: 55px;
        }
        .btn-mouse {
            flex: 1;
            background: #334155;
            color: white;
            border: none;
            border-radius: 12px;
            font-size: 1rem;
            font-weight: 600;
            touch-action: manipulation;
            cursor: pointer;
        }
        .btn-mouse:active {
            background: #64748b;
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-top">
            <div class="status-badge">HDMI: __STATUS_TEXT__</div>
            <button class="btn-action btn-stream" id="btn-toggle-stream" onclick="toggleStream()">👁️ Obraz: ZAP</button>
        </div>
        <div class="action-buttons">
            <a href="/hdmi/on" class="btn-action btn-on">Zapnout</a>
            <a href="/hdmi/off" class="btn-action btn-off">Vypnout</a>
            <button class="btn-action btn-audio" id="btn-audio" onclick="triggerAudioRefresh()">🔊 Zvuk</button>
        </div>
    </div>

    <div id="touchpad">
        <img id="screen-view" src="/api/screen/crop" alt="">
        <div class="crosshair"></div>
        <div class="touchpad-hint">Křížek = pozice myši</div>
    </div>

    <div class="mouse-buttons">
        <button class="btn-mouse" id="btn-left">Levé tl.</button>
        <button class="btn-mouse" id="btn-right">Pravé tl.</button>
    </div>

    <script>
        const pad = document.getElementById("touchpad");
        const screenView = document.getElementById("screen-view");
        const btnToggleStream = document.getElementById("btn-toggle-stream");

        let streamEnabled = true;
        let isFetchingScreen = false;
        const SENSITIVITY = 1.6;

        let lastX = 0, lastY = 0;
        let startX = 0, startY = 0;
        let startTime = 0;
        let accX = 0, accY = 0;
        let isSending = false;

        function flushMove() {
            if ((accX !== 0 || accY !== 0) && !isSending) {
                const x = accX;
                const y = accY;
                accX = 0;
                accY = 0;
                isSending = true;
                fetch('/api/mouse/move?dx=' + x + '&dy=' + y, { method: 'POST' })
                    .finally(() => { isSending = false; });
            }
        }
        setInterval(flushMove, 25);

        function updateScreen() {
            if (!streamEnabled || isFetchingScreen) return;
            isFetchingScreen = true;

            const nextImg = new Image();
            nextImg.onload = () => {
                screenView.src = nextImg.src;
                isFetchingScreen = false;
            };
            nextImg.onerror = () => {
                isFetchingScreen = false;
            };
            nextImg.src = '/api/screen/crop?t=' + Date.now();
        }
        setInterval(updateScreen, 450);

        function toggleStream() {
            streamEnabled = !streamEnabled;
            if (streamEnabled) {
                btnToggleStream.innerText = "👁️ Obraz: ZAP";
                btnToggleStream.style.backgroundColor = "#7c3aed";
                screenView.style.display = "block";
            } else {
                btnToggleStream.innerText = "👁️ Obraz: VYP";
                btnToggleStream.style.backgroundColor = "#475569";
                screenView.style.display = "none";
            }
        }

        pad.addEventListener("touchstart", (e) => {
            e.preventDefault();
            pad.classList.add("active");
            const t = e.touches[0];
            lastX = t.clientX;
            lastY = t.clientY;
            startX = t.clientX;
            startY = t.clientY;
            startTime = Date.now();
        }, { passive: false });

        pad.addEventListener("touchmove", (e) => {
            e.preventDefault();
            const t = e.touches[0];
            const dx = (t.clientX - lastX) * SENSITIVITY;
            const dy = (t.clientY - lastY) * SENSITIVITY;
            
            accX += Math.round(dx);
            accY += Math.round(dy);

            lastX = t.clientX;
            lastY = t.clientY;
        }, { passive: false });

        pad.addEventListener("touchend", (e) => {
            e.preventDefault();
            pad.classList.remove("active");
            const dist = Math.hypot(lastX - startX, lastY - startY);
            const duration = Date.now() - startTime;

            if (dist < 8 && duration < 250) {
                sendClick('left');
            }
            setTimeout(updateScreen, 60);
        }, { passive: false });

        function sendClick(btn) {
            fetch('/api/mouse/click?btn=' + btn, { method: 'POST' });
            setTimeout(updateScreen, 150);
        }

        function triggerAudioRefresh() {
            const btn = document.getElementById("btn-audio");
            const originalText = btn.innerText;
            btn.innerText = "⏳ Načítám...";
            btn.disabled = true;

            fetch('/api/audio/refresh', { method: 'POST' })
                .then(() => {
                    btn.innerText = "✓ Obnoveno";
                    setTimeout(() => {
                        btn.innerText = originalText;
                        btn.disabled = false;
                    }, 2000);
                });
        }

        document.getElementById("btn-left").addEventListener("click", () => sendClick('left'));
        document.getElementById("btn-right").addEventListener("click", () => sendClick('right'));
    </script>
</body>
</html>
"""

# --- HTTP HANDLER ---
class ServerHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._serve_dashboard()
        elif path == "/api/screen/crop":
            data = capture_centered_crop()
            if data:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error(503, "Snímek není k dispozici")
        elif path == "/hdmi/on":
            set_hdmi(True)
            self._redirect_home()
        elif path == "/hdmi/off":
            set_hdmi(False)
            self._redirect_home()
        elif path == "/audio/refresh":
            refresh_audio()
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

        elif path == "/api/audio/refresh":
            refresh_audio()
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

        html = HTML_TEMPLATE.replace("__STATUS_TEXT__", status_text).replace("__STATUS_COLOR__", status_color)

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        if len(args) > 0 and any(k in str(args[0]) for k in ["api/mouse", "api/screen"]):
            return
        print(f"[HTTP] {self.address_string()} - {format % args}")

def run_server(port):
    # Automatické vypnutí displeje při spuštění
    print("[STARTUP] Automatické vypínání HDMI monitoru...")
    set_hdmi(False)

    server_address = ("", port)
    httpd = ThreadingHTTPServer(server_address, ServerHandler)
    print(f"[SERVER] Běží na portu {port} (Touchpad + Screen Crop + HDMI)")
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
