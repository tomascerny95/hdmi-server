#!/usr/bin/env python3
import os
import io
import time
import argparse
import threading
import subprocess
from urllib.parse import urlparse, parse_qs
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from PIL import Image

PORT = 1111
USER = "cernytom"
RUNTIME_DIR = "/run/user/1000"

# --- ZJIŠTĚNÍ SKUTEČNÉHO ROZLIŠENÍ PLOCHY PŘES GRIM ---
def detect_desktop_resolution():
    temp = "/dev/shm/rpi_detect.png"
    try:
        cmd = [
            "runuser", "-u", USER, "--",
            "env", f"XDG_RUNTIME_DIR={RUNTIME_DIR}", "WAYLAND_DISPLAY=wayland-0",
            "grim", temp
        ] if os.getuid() == 0 else ["grim", temp]
        
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        if os.path.exists(temp):
            img = Image.open(temp)
            w, h = img.size
            os.remove(temp)
            print(f"[SCREEN] Reálné rozlišení plochy: {w}x{h}")
            return w, h
    except Exception:
        pass
    return 1920, 1080

screen_width, screen_height = detect_desktop_resolution()
cur_x = screen_width // 2
cur_y = screen_height // 2
pos_lock = threading.Lock()

# --- OVLÁDÁNÍ ZVUKU ---
def refresh_audio():
    commands = [
        "udevadm trigger --subsystem-match=sound",
        "alsactl init",
        "alsactl restore",
        f"runuser -u {USER} -- pkill -x wireplumber",
        f"runuser -u {USER} -- pkill -x pipewire",
        f"runuser -u {USER} -- pulseaudio -k"
    ]
    for cmd in commands:
        try:
            subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    try:
        cmd_sink = f"runuser -u {USER} -- env XDG_RUNTIME_DIR={RUNTIME_DIR} pactl list sinks short | grep -i hdmi | head -n 1 | awk '{{print $2}}'"
        res = subprocess.run(cmd_sink, shell=True, capture_output=True, text=True)
        hdmi_sink = res.stdout.strip()
        if hdmi_sink:
            subprocess.run(f"runuser -u {USER} -- env XDG_RUNTIME_DIR={RUNTIME_DIR} pactl set-default-sink {hdmi_sink}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def delayed_audio_refresh():
    time.sleep(1.5)
    refresh_audio()

# --- OVLÁDÁNÍ HDMI ---
def find_hdmi_status_path():
    default_path = "/sys/class/drm/card0-HDMI-A-1/status"
    if os.path.exists(default_path):
        return default_path
    import glob
    matches = glob.glob("/sys/class/drm/*HDMI*/status")
    return matches[0] if matches else default_path

HDMI_STATUS_PATH = find_hdmi_status_path()

def set_hdmi(enable: bool) -> bool:
    state_str = "on" if enable else "off"
    try:
        with open(HDMI_STATUS_PATH, "w") as f:
            f.write(state_str + "\n")
        if enable:
            threading.Thread(target=delayed_audio_refresh, daemon=True).start()
        return True
    except Exception:
        return False

def get_hdmi_status() -> str:
    try:
        with open(HDMI_STATUS_PATH, "r") as f:
            return f.read().strip()
    except Exception:
        return "neznámý"

# --- OVLÁDÁNÍ MYŠI: ABSOLUTNÍ TABLET (100% SYNCHRONIZACE S OBRAZOVKOU) ---
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
                # Absolutní ukazatel - myš je natvrdo na pixel přesně tam, kde říká Python
                cap = {
                    e.EV_ABS: [
                        (e.ABS_X, evdev.AbsInfo(value=cur_x, min=0, max=screen_width, fuzz=0, flat=0, resolution=1)),
                        (e.ABS_Y, evdev.AbsInfo(value=cur_y, min=0, max=screen_height, fuzz=0, flat=0, resolution=1))
                    ],
                    e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE]
                }
                self.ui = UInput(cap, name="RPi-Web-Touchpad-Abs")
                print("[MOUSE] Inicializována absolutní virtuální myš (nulový drift).")
            except Exception as ex:
                print(f"[MOUSE] Chyba inicializace: {ex}")

    def move_rel(self, dx: int, dy: int):
        global cur_x, cur_y
        with pos_lock:
            cur_x = max(0, min(screen_width, cur_x + dx))
            cur_y = max(0, min(screen_height, cur_y + dy))
            self._sync()

    def set_abs(self, x: int, y: int):
        global cur_x, cur_y
        with pos_lock:
            cur_x = max(0, min(screen_width, x))
            cur_y = max(0, min(screen_height, y))
            self._sync()

    def _sync(self):
        if self.ui:
            self.ui.write(e.EV_ABS, e.ABS_X, cur_x)
            self.ui.write(e.EV_ABS, e.ABS_Y, cur_y)
            self.ui.syn()

    def click(self, button: str = "left"):
        if self.ui:
            btn = e.BTN_LEFT if button == "left" else e.BTN_RIGHT
            self.ui.write(e.EV_KEY, btn, 1)
            self.ui.syn()
            self.ui.write(e.EV_KEY, btn, 0)
            self.ui.syn()

    def recenter(self):
        self.set_abs(screen_width // 2, screen_height // 2)

mouse = MouseController()

# --- SNÍMÁNÍ OBRAZOVKY (VÝŘEZ NEBO CELÁ OBRAZOVKA) ---
last_capture_time = 0
cached_image_bytes = None

def capture_screen(req_w, req_h, mode="crop"):
    global last_capture_time, cached_image_bytes

    now = time.time()
    if cached_image_bytes and (now - last_capture_time < 0.05):
        return cached_image_bytes

    temp_raw = "/dev/shm/rpi_crop.png"

    with pos_lock:
        mx, my = cur_x, cur_y

    if mode == "full":
        # Celá obrazovka
        geom_cmd = []
    else:
        # Zvětšený výřez s myší uprostřed
        crop_w = max(320, min(screen_width, req_w))
        crop_h = max(240, min(screen_height, req_h))

        ideal_x1 = mx - crop_w // 2
        ideal_y1 = my - crop_h // 2
        src_x1 = max(0, ideal_x1)
        src_y1 = max(0, ideal_y1)
        src_x2 = min(screen_width, ideal_x1 + crop_w)
        src_y2 = min(screen_height, ideal_y1 + crop_h)

        grab_w = src_x2 - src_x1
        grab_h = src_y2 - src_y1
        if grab_w <= 0 or grab_h <= 0:
            return cached_image_bytes
        geom_cmd = ["-g", f"{src_x1},{src_y1} {grab_w}x{grab_h}"]

    cmd = [
        "runuser", "-u", USER, "--",
        "env", f"XDG_RUNTIME_DIR={RUNTIME_DIR}", "WAYLAND_DISPLAY=wayland-0",
        "grim", "-c"
    ] + geom_cmd + [temp_raw] if os.getuid() == 0 else ["grim", "-c"] + geom_cmd + [temp_raw]

    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
        if not os.path.exists(temp_raw):
            return cached_image_bytes

        img = Image.open(temp_raw)

        if mode == "full":
            buf = io.BytesIO()
            # Mírná komprese pro plynulý náhled celé plochy
            img.thumbnail((720, 480))
            img.convert("RGB").save(buf, format="JPEG", quality=50, optimize=False)
            cached_image_bytes = buf.getvalue()
        else:
            canvas = Image.new("RGB", (crop_w, crop_h), (11, 17, 32))
            paste_x = src_x1 - ideal_x1
            paste_y = src_y1 - ideal_y1
            canvas.paste(img, (paste_x, paste_y))

            buf = io.BytesIO()
            canvas.save(buf, format="JPEG", quality=55, optimize=False)
            cached_image_bytes = buf.getvalue()

        last_capture_time = now
        return cached_image_bytes

    except Exception:
        return cached_image_bytes

# --- HTML ŠABLONA ---
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="cs">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>RPi Remote Control</title>
    <style>
        * { box-sizing: border-box; user-select: none; -webkit-user-select: none; }
        html, body {
            height: 100dvh;
            margin: 0;
            padding: 6px;
            background-color: #0b1120;
            color: #f8fafc;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }
        .header { flex-shrink: 0; display: flex; flex-direction: column; gap: 6px; margin-bottom: 6px; }
        .header-top { display: flex; justify-content: space-between; align-items: center; }
        .status-badge {
            padding: 4px 8px; border-radius: 6px; font-size: 0.8rem; font-weight: bold;
            color: __STATUS_COLOR__; border: 1px solid __STATUS_COLOR__;
        }
        .action-buttons { display: flex; gap: 6px; width: 100%; }
        .btn-action {
            flex: 1; padding: 8px 4px; border-radius: 8px; font-size: 0.8rem; font-weight: bold;
            border: none; cursor: pointer; color: white; text-align: center; text-decoration: none;
        }
        .btn-on { background-color: #16a34a; }
        .btn-off { background-color: #dc2626; }
        .btn-audio { background-color: #2563eb; }
        .btn-mode { background-color: #7c3aed; }
        .btn-center { background-color: #f59e0b; }

        #touchpad {
            flex: 1 1 0;
            min-height: 0;
            position: relative;
            background-color: #000;
            border-radius: 14px;
            border: 2px solid #334155;
            overflow: hidden;
            touch-action: none;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        #touchpad.active { border-color: #38bdf8; }
        #screen-view {
            width: 100%; height: 100%; object-fit: contain; position: absolute; top: 0; left: 0; pointer-events: none;
        }
        .mouse-buttons { flex-shrink: 0; display: flex; gap: 10px; margin-top: 6px; height: 52px; }
        .btn-mouse {
            flex: 1; background: #1e293b; color: white; border: 1px solid #475569;
            border-radius: 12px; font-size: 1rem; font-weight: 600; touch-action: manipulation; cursor: pointer;
        }
        .btn-mouse:active { background: #38bdf8; color: black; }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-top">
            <div class="status-badge">HDMI: __STATUS_TEXT__</div>
            <div style="display:flex; gap:6px;">
                <button class="btn-action btn-center" onclick="recenterMouse()">🎯 Střed</button>
                <button class="btn-action btn-mode" id="btn-mode" onclick="toggleViewMode()">🖥️ Celá plocha</button>
            </div>
        </div>
        <div class="action-buttons">
            <a href="/hdmi/on" class="btn-action btn-on">Zapnout</a>
            <a href="/hdmi/off" class="btn-action btn-off">Vypnout</a>
            <button class="btn-action btn-audio" id="btn-audio" onclick="triggerAudioRefresh()">🔊 Zvuk</button>
        </div>
    </div>

    <div id="touchpad">
        <img id="screen-view" src="/api/screen/view?w=360&h=480&mode=crop" alt="">
    </div>

    <div class="mouse-buttons">
        <button class="btn-mouse" id="btn-left">Levé tl.</button>
        <button class="btn-mouse" id="btn-right">Pravé tl.</button>
    </div>

    <script>
        const pad = document.getElementById("touchpad");
        const screenView = document.getElementById("screen-view");
        const btnMode = document.getElementById("btn-mode");

        let viewMode = "crop"; // "crop" nebo "full"
        let isFetchingScreen = false;
        let fetchWatchdog = null;
        const SENSITIVITY = 1.4;

        let lastX = 0, lastY = 0;
        let startX = 0, startY = 0;
        let startTime = 0;
        let accX = 0, accY = 0;
        let isSending = false;
        let isMouseDown = false;

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
            if (isFetchingScreen) return;
            isFetchingScreen = true;

            clearTimeout(fetchWatchdog);
            fetchWatchdog = setTimeout(() => { isFetchingScreen = false; }, 800);

            const w = Math.round(pad.clientWidth) || 360;
            const h = Math.round(pad.clientHeight) || 480;

            const nextImg = new Image();
            nextImg.onload = () => {
                screenView.src = nextImg.src;
                isFetchingScreen = false;
            };
            nextImg.onerror = () => { isFetchingScreen = false; };
            nextImg.src = `/api/screen/view?w=${w}&h=${h}&mode=${viewMode}&t=${Date.now()}`;
        }
        setInterval(updateScreen, 120);

        function toggleViewMode() {
            if (viewMode === "crop") {
                viewMode = "full";
                btnMode.innerText = "🔍 Lupa";
                btnMode.style.backgroundColor = "#2563eb";
            } else {
                viewMode = "crop";
                btnMode.innerText = "🖥️ Celá plocha";
                btnMode.style.backgroundColor = "#7c3aed";
            }
            updateScreen();
        }

        function recenterMouse() {
            fetch('/api/mouse/center', { method: 'POST' })
                .then(() => setTimeout(updateScreen, 50));
        }

        // V režimu Celá plocha: klepnutí skočí přímo na dané místo
        function handleAbsoluteTap(clientX, clientY) {
            if (viewMode !== "full") return false;
            const rect = pad.getBoundingClientRect();
            const relX = (clientX - rect.left) / rect.width;
            const relY = (clientY - rect.top) / rect.height;
            fetch(`/api/mouse/abs?rx=${relX}&ry=${relY}`, { method: 'POST' })
                .then(() => setTimeout(updateScreen, 50));
            return true;
        }

        // Dotyk
        pad.addEventListener("touchstart", (e) => {
            e.preventDefault();
            pad.classList.add("active");
            const t = e.touches[0];
            lastX = t.clientX; lastY = t.clientY;
            startX = t.clientX; startY = t.clientY;
            startTime = Date.now();
        }, { passive: false });

        pad.addEventListener("touchmove", (e) => {
            e.preventDefault();
            const t = e.touches[0];
            accX += Math.round((t.clientX - lastX) * SENSITIVITY);
            accY += Math.round((t.clientY - lastY) * SENSITIVITY);
            lastX = t.clientX; lastY = t.clientY;
        }, { passive: false });

        pad.addEventListener("touchend", (e) => {
            e.preventDefault();
            pad.classList.remove("active");
            const dist = Math.hypot(lastX - startX, lastY - startY);
            if (dist < 8 && (Date.now() - startTime) < 250) {
                if (!handleAbsoluteTap(lastX, lastY)) {
                    sendClick('left');
                }
            }
            setTimeout(updateScreen, 40);
        }, { passive: false });

        // Myš (PC)
        pad.addEventListener("mousedown", (e) => {
            isMouseDown = true;
            pad.classList.add("active");
            lastX = e.clientX; lastY = e.clientY;
            startX = e.clientX; startY = e.clientY;
            startTime = Date.now();
        });

        window.addEventListener("mousemove", (e) => {
            if (!isMouseDown) return;
            accX += Math.round((e.clientX - lastX) * SENSITIVITY);
            accY += Math.round((e.clientY - lastY) * SENSITIVITY);
            lastX = e.clientX; lastY = e.clientY;
        });

        window.addEventListener("mouseup", (e) => {
            if (!isMouseDown) return;
            isMouseDown = false;
            pad.classList.remove("active");
            const dist = Math.hypot(lastX - startX, lastY - startY);
            if (dist < 8 && (Date.now() - startTime) < 250) {
                if (!handleAbsoluteTap(lastX, lastY)) {
                    sendClick('left');
                }
            }
            setTimeout(updateScreen, 40);
        });

        function sendClick(btn) {
            fetch('/api/mouse/click?btn=' + btn, { method: 'POST' });
            setTimeout(updateScreen, 60);
        }

        function triggerAudioRefresh() {
            const btn = document.getElementById("btn-audio");
            const originalText = btn.innerText;
            btn.innerText = "⏳ Načítám...";
            btn.disabled = true;

            fetch('/api/audio/refresh', { method: 'POST' })
                .then(() => {
                    btn.innerText = "✓ Obnoveno";
                    setTimeout(() => { btn.innerText = originalText; btn.disabled = false; }, 2000);
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
        elif path in ["/api/screen/view", "/api/screen/crop"]:
            params = parse_qs(parsed.query)
            try:
                w = int(params.get("w", [360])[0])
                h = int(params.get("h", [480])[0])
            except Exception:
                w, h = 360, 480
            mode = params.get("mode", ["crop"])[0]

            data = capture_screen(w, h, mode)
            if data:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error(503)
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
                mouse.move_rel(dx, dy)
                self._send_status(204)
            except Exception:
                self._send_status(400)

        elif path == "/api/mouse/abs":
            try:
                rx = float(params.get("rx", [0.5])[0])
                ry = float(params.get("ry", [0.5])[0])
                mouse.set_abs(int(rx * screen_width), int(ry * screen_height))
                self._send_status(204)
            except Exception:
                self._send_status(400)

        elif path == "/api/mouse/click":
            btn = params.get("btn", ["left"])[0]
            mouse.click(btn)
            self._send_status(204)

        elif path == "/api/mouse/center":
            mouse.recenter()
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
    print("[STARTUP] Automatické vypínání HDMI monitoru...")
    set_hdmi(False)

    server_address = ("", port)
    httpd = ThreadingHTTPServer(server_address, ServerHandler)
    print(f"[SERVER] Běží na portu {port} (Absolutní tablet + Celá plocha / Lupa)")
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
