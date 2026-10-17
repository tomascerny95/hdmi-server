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

# --- ZJIŠTĚNÍ ROZLIŠENÍ PLOCHY ---
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

# --- OVLÁDÁNÍ MYŠI A KLÁVESNICE (EVDEV) ---
try:
    import evdev
    from evdev import UInput, ecodes as e
    HAS_EVDEV = True
except ImportError:
    HAS_EVDEV = False

CHAR_KEY_MAP = {}
SPECIAL_KEY_MAP = {}
VALID_KEY_CODES = []

if HAS_EVDEV:
    # 1. Speciální klávesy
    SPECIAL_KEY_MAP = {
        'Enter': e.KEY_ENTER,
        'Backspace': e.KEY_BACKSPACE,
        'Tab': e.KEY_TAB,
        'Esc': e.KEY_ESC,
        'Space': e.KEY_SPACE,
        'Up': e.KEY_UP,
        'Down': e.KEY_DOWN,
        'Left': e.KEY_LEFT,
        'Right': e.KEY_RIGHT
    }

    # 2. Písmena a čísla
    for ch in "abcdefghijklmnopqrstuvwxyz":
        key_code = getattr(e, f"KEY_{ch.upper()}", None)
        if key_code:
            CHAR_KEY_MAP[ch] = (key_code, False)
            CHAR_KEY_MAP[ch.upper()] = (key_code, True)
            VALID_KEY_CODES.append(key_code)

    for i in range(10):
        key_code = getattr(e, f"KEY_{i}", None)
        if key_code:
            CHAR_KEY_MAP[str(i)] = (key_code, False)
            VALID_KEY_CODES.append(key_code)

    # 3. Znaky
    symbols = {
        ' ': (e.KEY_SPACE, False),
        '\n': (e.KEY_ENTER, False),
        '\t': (e.KEY_TAB, False),
        '.': (e.KEY_DOT, False),
        ',': (e.KEY_COMMA, False),
        '-': (e.KEY_MINUS, False),
        '_': (e.KEY_MINUS, True),
        '/': (e.KEY_SLASH, False),
        ':': (e.KEY_SEMICOLON, True),
        ';': (e.KEY_SEMICOLON, False),
        '=': (e.KEY_EQUAL, False),
        '+': (e.KEY_EQUAL, True),
        '?': (e.KEY_SLASH, True),
        '!': (e.KEY_1, True)
    }
    CHAR_KEY_MAP.update(symbols)

    VALID_KEY_CODES.extend([
        e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE,
        e.KEY_LEFTSHIFT, e.KEY_ENTER, e.KEY_BACKSPACE,
        e.KEY_TAB, e.KEY_ESC, e.KEY_SPACE, e.KEY_DOT,
        e.KEY_COMMA, e.KEY_MINUS, e.KEY_SLASH, e.KEY_SEMICOLON,
        e.KEY_EQUAL, e.KEY_UP, e.KEY_DOWN, e.KEY_LEFT, e.KEY_RIGHT
    ])
    VALID_KEY_CODES = list(set(VALID_KEY_CODES))

class InputController:
    def __init__(self):
        self.ui = None
        if HAS_EVDEV:
            try:
                # Osvědčená relativní myš + bezpečný seznam kláves
                cap = {
                    e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL],
                    e.EV_KEY: VALID_KEY_CODES
                }
                self.ui = UInput(cap, name="RPi-Web-Remote-Touchpad")
                print("[INPUT] Ovladač myši a klávesnice úspěšně vytvořen.")
            except Exception as ex:
                print(f"[INPUT] Chyba inicializace evdev: {ex}")

    def move(self, dx: int, dy: int):
        global cur_x, cur_y
        with pos_lock:
            cur_x = max(0, min(screen_width, cur_x + dx))
            cur_y = max(0, min(screen_height, cur_y + dy))

        if self.ui:
            self.ui.write(e.EV_REL, e.REL_X, dx)
            self.ui.write(e.EV_REL, e.REL_Y, dy)
            self.ui.syn()

    def click(self, button: str = "left"):
        if self.ui:
            btn = e.BTN_LEFT if button == "left" else e.BTN_RIGHT
            self.ui.write(e.EV_KEY, btn, 1)
            self.ui.syn()
            time.sleep(0.01)
            self.ui.write(e.EV_KEY, btn, 0)
            self.ui.syn()

    def send_key(self, key_name: str):
        if self.ui and key_name in SPECIAL_KEY_MAP:
            code = SPECIAL_KEY_MAP[key_name]
            self.ui.write(e.EV_KEY, code, 1)
            self.ui.syn()
            time.sleep(0.01)
            self.ui.write(e.EV_KEY, code, 0)
            self.ui.syn()

    def type_text(self, text: str):
        if not self.ui:
            return
        for char in text:
            if char in CHAR_KEY_MAP:
                code, shift = CHAR_KEY_MAP[char]
                if shift:
                    self.ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
                self.ui.write(e.EV_KEY, code, 1)
                self.ui.syn()
                time.sleep(0.005)
                self.ui.write(e.EV_KEY, code, 0)
                if shift:
                    self.ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
                self.ui.syn()
                time.sleep(0.005)

controller = InputController()

# --- SNÍMÁNÍ OBRAZOVKY ---
last_capture_time = 0
cached_image_bytes = None

def capture_screen(req_w, req_h):
    global last_capture_time, cached_image_bytes

    now = time.time()
    if cached_image_bytes and (now - last_capture_time < 0.05):
        return cached_image_bytes

    temp_raw = "/dev/shm/rpi_crop.png"

    with pos_lock:
        mx, my = cur_x, cur_y

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

    geom = f"{src_x1},{src_y1} {grab_w}x{grab_h}"

    cmd = [
        "runuser", "-u", USER, "--",
        "env", f"XDG_RUNTIME_DIR={RUNTIME_DIR}", "WAYLAND_DISPLAY=wayland-0",
        "grim", "-c", "-g", geom, temp_raw
    ] if os.getuid() == 0 else ["grim", "-c", "-g", geom, temp_raw]

    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
        if not os.path.exists(temp_raw):
            return cached_image_bytes

        img = Image.open(temp_raw)
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
        .top-tools { display: flex; gap: 6px; }
        .action-buttons { display: flex; gap: 6px; width: 100%; }
        .btn-action {
            flex: 1; padding: 8px 4px; border-radius: 8px; font-size: 0.8rem; font-weight: bold;
            border: none; cursor: pointer; color: white; text-align: center; text-decoration: none;
        }
        .btn-on { background-color: #16a34a; }
        .btn-off { background-color: #dc2626; }
        .btn-audio { background-color: #2563eb; }
        .btn-kbd { background-color: #0ea5e9; }
        .btn-stream { background-color: #7c3aed; }
        .btn-close { background-color: #991b1b; }

        #keyboard-panel {
            display: none;
            background: #1e293b;
            padding: 8px;
            border-radius: 10px;
            margin-bottom: 6px;
            flex-direction: column;
            gap: 6px;
            border: 1px solid #334155;
        }
        .kbd-row { display: flex; gap: 6px; }
        #kbd-input {
            flex: 1; padding: 8px 12px; border-radius: 8px; border: 1px solid #475569;
            background: #0f172a; color: white; font-size: 1rem; outline: none;
        }
        .btn-kbd-key {
            padding: 6px 12px; border-radius: 8px; border: 1px solid #475569;
            background: #334155; color: white; font-size: 0.85rem; font-weight: bold; cursor: pointer;
        }
        .btn-kbd-key:active { background: #38bdf8; color: black; }

        #touchpad {
            flex: 1 1 0;
            min-height: 0;
            position: relative;
            background-color: #000;
            border-radius: 14px;
            border: 2px solid #334155;
            overflow: hidden;
            touch-action: none;
            cursor: crosshair;
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
            <div class="top-tools">
                <button class="btn-action btn-kbd" onclick="toggleKeyboard()">⌨️ Klávesnice</button>
                <button class="btn-action btn-stream" id="btn-stream" onclick="toggleStream()">👁️ Obraz: ZAP</button>
                <button class="btn-action btn-close" onclick="closeApp()">❌ Zavřít</button>
            </div>
        </div>
        <div class="action-buttons">
            <a href="/hdmi/on" class="btn-action btn-on">Zapnout</a>
            <a href="/hdmi/off" class="btn-action btn-off">Vypnout</a>
            <button class="btn-action btn-audio" id="btn-audio" onclick="triggerAudioRefresh()">🔊 Zvuk</button>
        </div>

        <div id="keyboard-panel">
            <form onsubmit="submitText(event)" class="kbd-row">
                <input type="text" id="kbd-input" placeholder="Zde pište text do Raspberry..." autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false">
                <button type="submit" class="btn-kbd-key" style="background:#0ea5e9;">Odeslat</button>
            </form>
            <div class="kbd-row">
                <button class="btn-kbd-key" style="flex:1;" onclick="sendKey('Enter')">↵ Enter</button>
                <button class="btn-kbd-key" style="flex:1;" onclick="sendKey('Backspace')">⌫ Smazat</button>
                <button class="btn-kbd-key" style="flex:1;" onclick="sendKey('Tab')">Tab</button>
                <button class="btn-kbd-key" style="flex:1;" onclick="sendKey('Esc')">Esc</button>
            </div>
        </div>
    </div>

    <div id="touchpad">
        <img id="screen-view" src="/api/screen/crop?w=360&h=480" alt="">
    </div>

    <div class="mouse-buttons">
        <button class="btn-mouse" id="btn-left">Levé tl.</button>
        <button class="btn-mouse" id="btn-right">Pravé tl.</button>
    </div>

    <script>
        const pad = document.getElementById("touchpad");
        const screenView = document.getElementById("screen-view");
        const btnStream = document.getElementById("btn-stream");
        const kbdPanel = document.getElementById("keyboard-panel");
        const kbdInput = document.getElementById("kbd-input");

        let streamEnabled = true;
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
            if (!streamEnabled || isFetchingScreen) return;
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
            nextImg.src = `/api/screen/crop?w=${w}&h=${h}&t=${Date.now()}`;
        }
        setInterval(updateScreen, 120);

        function toggleKeyboard() {
            if (kbdPanel.style.display === "flex") {
                kbdPanel.style.display = "none";
                kbdInput.blur();
            } else {
                kbdPanel.style.display = "flex";
                setTimeout(() => kbdInput.focus(), 50);
            }
        }

        function submitText(e) {
            e.preventDefault();
            const text = kbdInput.value;
            if (text) {
                fetch('/api/keyboard/text', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: text })
                });
                kbdInput.value = "";
            }
            setTimeout(updateScreen, 80);
        }

        function sendKey(keyName) {
            fetch('/api/keyboard/key?key=' + keyName, { method: 'POST' });
            setTimeout(updateScreen, 80);
        }

        function toggleStream() {
            streamEnabled = !streamEnabled;
            if (streamEnabled) {
                btnStream.innerText = "👁️ Obraz: ZAP";
                btnStream.style.backgroundColor = "#7c3aed";
                screenView.style.display = "block";
                updateScreen();
            } else {
                btnStream.innerText = "👁️ Obraz: VYP";
                btnStream.style.backgroundColor = "#475569";
                screenView.style.display = "none";
            }
        }

        function closeApp() {
            if (confirm("Chcete stránku zavřít?")) {
                window.open('', '_self', '');
                window.close();
                document.body.innerHTML = `
                    <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100dvh;color:#94a3b8;font-size:1.2rem;text-align:center;padding:20px;">
                        <span>Aplikace byla ukončena.</span>
                        <span style="font-size:0.9rem;margin-top:10px;color:#64748b;">Nyní můžete zavřít tuto záložku v prohlížeči.</span>
                    </div>`;
            }
        }

        // Dotykové ovládání (mobil)
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
                sendClick('left');
            }
            setTimeout(updateScreen, 40);
        }, { passive: false });

        // Ovládání pro PC myš
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
                sendClick('left');
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
        elif path in ["/api/screen/crop", "/api/screen/view"]:
            params = parse_qs(parsed.query)
            try:
                w = int(params.get("w", [360])[0])
                h = int(params.get("h", [480])[0])
            except Exception:
                w, h = 360, 480

            data = capture_screen(w, h)
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
                controller.move(dx, dy)
                self._send_status(204)
            except Exception:
                self._send_status(400)

        elif path == "/api/mouse/click":
            btn = params.get("btn", ["left"])[0]
            controller.click(btn)
            self._send_status(204)

        elif path == "/api/keyboard/key":
            key_name = params.get("key", [""])[0]
            if key_name:
                controller.send_key(key_name)
            self._send_status(204)

        elif path == "/api/keyboard/text":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                import json
                data = json.loads(body)
                text = data.get("text", "")
                if text:
                    controller.type_text(text)
                self._send_status(204)
            except Exception:
                self._send_status(400)

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
    print(f"[SERVER] Běží na portu {port} (Plně funkční Touchpad + Klávesnice)")
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
