#!/usr/bin/env python3
import os
import pwd
import time
import argparse
import threading
import subprocess
from urllib.parse import urlparse, parse_qs
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

PORT = 1111

# --- NAČTENÍ POUZE Z PROMĚNNÝCH PROSTŘEDÍ (BEZ NATVRDO ZAPSANÝCH HODNOT) ---
USER = os.environ.get("RPI_USER")
USER_UID = os.environ.get("RPI_UID")

# Pokud proměnné nejsou nastaveny, zjistí se dynamicky ze systému:
if not USER or not USER_UID:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        USER = sudo_user
        USER_UID = str(pwd.getpwnam(sudo_user).pw_uid)
    else:
        USER = os.environ.get("USER")
        USER_UID = str(os.getuid())

RUNTIME_DIR = f"/run/user/{USER_UID}"

print(f"[CONFIG] Uživatel: {USER} | UID: {USER_UID} | Runtime: {RUNTIME_DIR}")

# Kontrola root oprávnění
if os.getuid() != 0:
    print("\n" + "!" * 65)
    print(" [VAROVÁNÍ] Skript neběží pod SUDO!")
    print(" Linux nedovolí ovládat myš bez oprávnění root.")
    print(" Spusťte skript příkazem:  sudo python3 " + os.path.basename(__file__))
    print("!" * 65 + "\n")

# --- OVLÁDÁNÍ ZVUKU ---
def refresh_audio():
    for cmd in ["udevadm trigger --subsystem-match=sound", "alsactl restore"]:
        try:
            subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    try:
        cmd_wp = f"runuser -u {USER} -- env XDG_RUNTIME_DIR={RUNTIME_DIR} systemctl --user restart wireplumber"
        subprocess.run(cmd_wp, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    time.sleep(1.5)

    try:
        env_prefix = f"runuser -u {USER} -- env XDG_RUNTIME_DIR={RUNTIME_DIR}"
        cmd_sink = f"{env_prefix} pactl list sinks short"
        res = subprocess.run(cmd_sink, shell=True, capture_output=True, text=True)
        
        hdmi_sink = None
        for line in res.stdout.strip().splitlines():
            if "hdmi" in line.lower():
                parts = line.split()
                if len(parts) >= 2:
                    hdmi_sink = parts[1]
                    break

        if hdmi_sink:
            subprocess.run(f"{env_prefix} pactl set-default-sink {hdmi_sink}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            cmd_inputs = f"{env_prefix} pactl list sink-inputs short"
            res_inputs = subprocess.run(cmd_inputs, shell=True, capture_output=True, text=True)
            for line in res_inputs.stdout.strip().splitlines():
                parts = line.split()
                if parts:
                    input_id = parts[0]
                    subprocess.run(f"{env_prefix} pactl move-sink-input {input_id} {hdmi_sink}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"[AUDIO] Přepnuto na HDMI: {hdmi_sink}")
    except Exception as ex:
        print(f"[AUDIO] Chyba při obnově audia: {ex}")

def delayed_audio_refresh():
    time.sleep(5.0)
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

# --- OVLÁDÁNÍ MYŠI A KLÁVESNICE ---
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

    for ch in "abcdefghijklmnopqrstuvwx":
        key_code = getattr(e, f"KEY_{ch.upper()}", None)
        if key_code:
            CHAR_KEY_MAP[ch] = (key_code, False)
            CHAR_KEY_MAP[ch.upper()] = (key_code, True)
            VALID_KEY_CODES.append(key_code)

    # QWERTZ FIX
    CHAR_KEY_MAP['y'] = (e.KEY_Z, False)
    CHAR_KEY_MAP['Y'] = (e.KEY_Z, True)
    CHAR_KEY_MAP['z'] = (e.KEY_Y, False)
    CHAR_KEY_MAP['Z'] = (e.KEY_Y, True)
    VALID_KEY_CODES.extend([e.KEY_Y, e.KEY_Z])

    for i in range(10):
        key_code = getattr(e, f"KEY_{i}", None)
        if key_code:
            CHAR_KEY_MAP[str(i)] = (key_code, False)
            VALID_KEY_CODES.append(key_code)

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

class MouseKeyboardController:
    def __init__(self):
        self.ui = None
        if HAS_EVDEV:
            try:
                cap = {
                    e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL],
                    e.EV_KEY: VALID_KEY_CODES
                }
                self.ui = UInput(cap, name="RPi-Web-Remote-Mouse")
                print("[INPUT] Virtuální myš a klávesnice úspěšně vytvořena!")
            except Exception as ex:
                print(f"[CHYBA INPUTU] Nelze otevřít evdev: {ex}")

    def move(self, dx: int, dy: int):
        if self.ui:
            self.ui.write(e.EV_REL, e.REL_X, dx)
            self.ui.write(e.EV_REL, e.REL_Y, dy)
            self.ui.syn()

    def scroll(self, dy: int):
        if self.ui:
            self.ui.write(e.EV_REL, e.REL_WHEEL, dy)
            self.ui.syn()

    def button_event(self, button: str, state: int):
        if self.ui:
            btn = e.BTN_LEFT if button == "left" else e.BTN_RIGHT
            self.ui.write(e.EV_KEY, btn, state)
            self.ui.syn()

    def click(self, button: str = "left"):
        self.button_event(button, 1)
        time.sleep(0.01)
        self.button_event(button, 0)

    def send_key(self, key_name: str):
        if self.ui and key_name in SPECIAL_KEY_MAP:
            code = SPECIAL_KEY_MAP[key_name]
            self.ui.write(e.EV_KEY, code, 1)
            self.ui.syn()
            time.sleep(0.01)
            self.ui.write(e.EV_KEY, code, 0)
            self.ui.syn()

    def type_text(self, text: str):
        cmd = [
            "runuser", "-u", USER, "--",
            "env", f"XDG_RUNTIME_DIR={RUNTIME_DIR}", "WAYLAND_DISPLAY=wayland-0",
            "wtype", text
        ] if os.getuid() == 0 else ["wtype", text]

        try:
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
            if res.returncode == 0:
                return
        except Exception:
            pass

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

controller = MouseKeyboardController()

# --- WEBOVÉ ROZHRANÍ ---
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="cs">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>RPi Remote Touchpad</title>
    <style>
        * { box-sizing: border-box; user-select: none; -webkit-user-select: none; }
        html, body {
            height: 100dvh;
            margin: 0;
            padding: 8px;
            background-color: #0b1120;
            color: #f8fafc;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }
        .header { flex-shrink: 0; display: flex; flex-direction: column; gap: 8px; margin-bottom: 8px; }
        .header-top { display: flex; justify-content: space-between; align-items: center; }
        .status-badge {
            padding: 6px 10px; border-radius: 8px; font-size: 0.85rem; font-weight: bold;
            color: __STATUS_COLOR__; border: 1px solid __STATUS_COLOR__;
        }
        .top-tools { display: flex; gap: 8px; }
        .action-buttons { display: flex; gap: 8px; width: 100%; }
        .btn-action {
            flex: 1; padding: 10px 6px; border-radius: 8px; font-size: 0.85rem; font-weight: bold;
            border: none; cursor: pointer; color: white; text-align: center; text-decoration: none;
        }
        .btn-on { background-color: #16a34a; }
        .btn-off { background-color: #dc2626; }
        .btn-audio { background-color: #2563eb; }
        .btn-kbd { background-color: #0ea5e9; }
        .btn-close { background-color: #991b1b; }

        #keyboard-panel {
            display: none;
            background: #1e293b;
            padding: 8px;
            border-radius: 10px;
            flex-direction: column;
            gap: 8px;
            border: 1px solid #334155;
        }
        .kbd-row { display: flex; gap: 8px; }
        #kbd-input {
            flex: 1; padding: 10px 14px; border-radius: 8px; border: 1px solid #475569;
            background: #0f172a; color: white; font-size: 1rem; outline: none;
        }
        .btn-kbd-key {
            padding: 8px 12px; border-radius: 8px; border: 1px solid #475569;
            background: #334155; color: white; font-size: 0.9rem; font-weight: bold; cursor: pointer;
            text-align: center;
        }
        .btn-kbd-key:active { background: #38bdf8; color: black; }
        .btn-arrow { font-size: 1.1rem; flex: 1; }

        /* TOUCHPAD PLOCHA */
        #touchpad {
            flex: 1 1 0;
            min-height: 0;
            background: #020617;
            border-radius: 16px;
            border: 2px solid #334155;
            touch-action: none;
            cursor: crosshair;
            display: flex;
            align-items: center;
            justify-content: center;
            position: relative;
        }
        #touchpad.active {
            border-color: #38bdf8;
            background: #0f172a;
        }
        #touchpad.dragging {
            border-color: #f59e0b;
            background: #171c26;
        }

        .touchpad-hints {
            color: #475569;
            font-size: 0.8rem;
            pointer-events: none;
            text-align: center;
            line-height: 1.5;
        }

        .mouse-buttons { flex-shrink: 0; display: flex; gap: 12px; margin-top: 8px; height: 56px; }
        .btn-mouse {
            flex: 1; background: #1e293b; color: white; border: 1px solid #475569;
            border-radius: 14px; font-size: 1.05rem; font-weight: 600; touch-action: manipulation; cursor: pointer;
        }
        .btn-mouse:active, .btn-mouse.held {
            background: #38bdf8; color: black; border-color: #38bdf8;
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-top">
            <div class="status-badge">HDMI: __STATUS_TEXT__</div>
            <div class="top-tools">
                <button class="btn-action btn-kbd" onclick="toggleKeyboard()">⌨️ Klávesnice</button>
                <button class="btn-action btn-close" onclick="closeApp()">❌ Zavřít</button>
            </div>
        </div>
        <div class="action-buttons">
            <a href="/hdmi/on" class="btn-action btn-on">Zapnout TV</a>
            <a href="/hdmi/off" class="btn-action btn-off">Vypnout TV</a>
            <button class="btn-action btn-audio" id="btn-audio" onclick="triggerAudioRefresh()">🔊 Zvuk</button>
        </div>

        <!-- PANEL KLÁVESNICE -->
        <div id="keyboard-panel">
            <form onsubmit="submitText(event)" class="kbd-row">
                <input type="text" id="kbd-input" placeholder="Zde pište text do Raspberry..." autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false">
                <button type="submit" class="btn-kbd-key" style="background:#0ea5e9;">Odeslat</button>
            </form>

            <!-- Řádek 1: Běžné klávesy -->
            <div class="kbd-row">
                <button class="btn-kbd-key" style="flex:1;" onclick="sendKey('Esc')">Esc</button>
                <button class="btn-kbd-key" style="flex:1;" onclick="sendKey('Tab')">Tab</button>
                <button class="btn-kbd-key" style="flex:1.2;" onclick="sendKey('Backspace')">⌫ Smazat</button>
                <button class="btn-kbd-key" style="flex:1.4; background:#16a34a;" onclick="sendKey('Enter')">↵ Enter</button>
            </div>

            <!-- Řádek 2: Mezerník + Šipky -->
            <div class="kbd-row">
                <button class="btn-kbd-key" style="flex:2.5;" onclick="sendKey('Space')">␣ Mezerník</button>
                <button class="btn-kbd-key btn-arrow" onclick="sendKey('Left')">←</button>
                <button class="btn-kbd-key btn-arrow" onclick="sendKey('Up')">↑</button>
                <button class="btn-kbd-key btn-arrow" onclick="sendKey('Down')">↓</button>
                <button class="btn-kbd-key btn-arrow" onclick="sendKey('Right')">→</button>
            </div>
        </div>
    </div>

    <!-- TOUCHPAD PLOCHA -->
    <div id="touchpad">
        <div class="touchpad-hints">
            <span>👆 1 prst: Pohyb myši / Kliknutí</span><br>
            <span>✌️ 2 prsty: Kolečko myši (Scroll)</span><br>
            <span>✊ 2× klepnout a táhnout: Přichycení okna</span>
        </div>
    </div>

    <div class="mouse-buttons">
        <button class="btn-mouse" id="btn-left">Levé tlačítko</button>
        <button class="btn-mouse" id="btn-right">Pravé tlačítko</button>
    </div>

    <script>
        const pad = document.getElementById("touchpad");
        const kbdPanel = document.getElementById("keyboard-panel");
        const kbdInput = document.getElementById("kbd-input");

        const SENSITIVITY = 1.3;
        let lastX = 0, lastY = 0;
        let startX = 0, startY = 0;
        let startTime = 0;
        let accX = 0, accY = 0;
        let isSending = false;

        // Kolečko (2 prsty)
        let isTwoFinger = false;
        let lastTwoY = 0;
        let accScroll = 0;

        // Přichycení (Drag & drop)
        let lastTapTime = 0;
        let isDragging = false;

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
        setInterval(flushMove, 16);

        function flushScroll() {
            if (accScroll !== 0) {
                const step = Math.trunc(accScroll / 20);
                if (step !== 0) {
                    accScroll -= (step * 20);
                    fetch('/api/mouse/wheel?dy=' + step, { method: 'POST' });
                }
            }
        }
        setInterval(flushScroll, 30);

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
        }

        function sendKey(keyName) {
            fetch('/api/keyboard/key?key=' + keyName, { method: 'POST' });
        }

        function closeApp() {
            if (confirm("Chcete stránku zavřít?")) {
                window.open('', '_self', '');
                window.close();
                document.body.innerHTML = `
                    <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100dvh;color:#94a3b8;font-size:1.2rem;text-align:center;padding:20px;">
                        <span>Aplikace byla ukončena.</span>
                    </div>`;
            }
        }

        // --- DOTYKOVÉ OVLÁDÁNÍ (MOBIL) ---
        pad.addEventListener("touchstart", (e) => {
            e.preventDefault();

            // 1. REŽIM KOLEČKA (2 PRSTY)
            if (e.touches.length === 2) {
                isTwoFinger = true;
                lastTwoY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
                accScroll = 0;
                return;
            }

            // 2. REŽIM 1 PRST
            isTwoFinger = false;
            const t = e.touches[0];
            lastX = t.clientX; lastY = t.clientY;
            startX = t.clientX; startY = t.clientY;
            const now = Date.now();

            if (now - lastTapTime < 280) {
                isDragging = true;
                pad.classList.add("dragging");
                fetch('/api/mouse/press?btn=left&state=1', { method: 'POST' });
            } else {
                pad.classList.add("active");
            }
            startTime = now;
        }, { passive: false });

        pad.addEventListener("touchmove", (e) => {
            e.preventDefault();

            // 2 PRSTY = KOLEČKO
            if (e.touches.length === 2) {
                const currentY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
                const diff = currentY - lastTwoY;
                lastTwoY = currentY;
                accScroll += diff;
                return;
            }

            if (isTwoFinger) return;

            // 1 PRST = POHYB / TAŽENÍ
            const t = e.touches[0];
            accX += Math.round((t.clientX - lastX) * SENSITIVITY);
            accY += Math.round((t.clientY - lastY) * SENSITIVITY);
            lastX = t.clientX; lastY = t.clientY;
        }, { passive: false });

        pad.addEventListener("touchend", (e) => {
            e.preventDefault();

            if (isTwoFinger) {
                if (e.touches.length < 2) isTwoFinger = false;
                return;
            }

            pad.classList.remove("active");

            if (isDragging) {
                isDragging = false;
                pad.classList.remove("dragging");
                fetch('/api/mouse/press?btn=left&state=0', { method: 'POST' });
                lastTapTime = 0;
                return;
            }

            const dist = Math.hypot(lastX - startX, lastY - startY);
            const now = Date.now();
            if (dist < 10 && (now - startTime) < 250) {
                sendClick('left');
                lastTapTime = now;
            } else {
                lastTapTime = 0;
            }
        }, { passive: false });

        // --- PODPORA KOLEČKA PŘI TESTU Z PC ---
        pad.addEventListener("wheel", (e) => {
            e.preventDefault();
            const step = e.deltaY < 0 ? 1 : -1;
            fetch('/api/mouse/wheel?dy=' + step, { method: 'POST' });
        }, { passive: false });

        // --- SPODNÍ TLAČÍTKA S PODPOROU DRŽENÍ ---
        function setupHoldButton(btnId, buttonName) {
            const btn = document.getElementById(btnId);

            btn.addEventListener("touchstart", (e) => {
                e.preventDefault();
                btn.classList.add("held");
                fetch(`/api/mouse/press?btn=${buttonName}&state=1`, { method: 'POST' });
            }, { passive: false });

            btn.addEventListener("touchend", (e) => {
                e.preventDefault();
                btn.classList.remove("held");
                fetch(`/api/mouse/press?btn=${buttonName}&state=0`, { method: 'POST' });
            }, { passive: false });

            btn.addEventListener("mousedown", (e) => {
                btn.classList.add("held");
                fetch(`/api/mouse/press?btn=${buttonName}&state=1`, { method: 'POST' });
            });
            window.addEventListener("mouseup", (e) => {
                if (btn.classList.contains("held")) {
                    btn.classList.remove("held");
                    fetch(`/api/mouse/press?btn=${buttonName}&state=0`, { method: 'POST' });
                }
            });
        }

        setupHoldButton("btn-left", "left");
        setupHoldButton("btn-right", "right");

        function sendClick(btn) {
            fetch('/api/mouse/click?btn=' + btn, { method: 'POST' });
        }

        function triggerAudioRefresh() {
            const btn = document.getElementById("btn-audio");
            const originalText = btn.innerText;
            btn.innerText = "⏳ Obnovuji...";
            btn.disabled = true;

            fetch('/api/audio/refresh', { method: 'POST' })
                .then(() => {
                    btn.innerText = "✓ Zvuk OK";
                    setTimeout(() => { btn.innerText = originalText; btn.disabled = false; }, 2000);
                })
                .catch(() => {
                    btn.innerText = "Chyba";
                    setTimeout(() => { btn.innerText = originalText; btn.disabled = false; }, 2000);
                });
        }
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

        # 1. Pohyb myši
        if path == "/api/mouse/move":
            try:
                dx = int(params.get("dx", [0])[0])
                dy = int(params.get("dy", [0])[0])
                controller.move(dx, dy)
                self._send_status(204)
            except Exception:
                self._send_status(400)

        # 2. Kolečko myši (Scroll)
        elif path == "/api/mouse/wheel":
            try:
                dy = int(params.get("dy", [0])[0])
                controller.scroll(dy)
                self._send_status(204)
            except Exception:
                self._send_status(400)

        # 3. Držení tlačítka (Přichycení)
        elif path == "/api/mouse/press":
            try:
                btn = params.get("btn", ["left"])[0]
                state = int(params.get("state", [1])[0])
                controller.button_event(btn, state)
                self._send_status(204)
            except Exception:
                self._send_status(400)

        # 4. Jednorázový klik
        elif path == "/api/mouse/click":
            btn = params.get("btn", ["left"])[0]
            controller.click(btn)
            self._send_status(204)

        # 5. Klávesnice (včetně šipek a mezerníku)
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
        # Filtruje logy pohybu a kolečka
        if len(args) > 0 and any(k in str(args[0]) for k in ["api/mouse/move", "api/mouse/wheel"]):
            return
        print(f"[HTTP] {self.address_string()} - {format % args}")

def run_server(port):
    print("[STARTUP] Automatické vypínání HDMI monitoru...")
    set_hdmi(False)

    server_address = ("", port)
    httpd = ThreadingHTTPServer(server_address, ServerHandler)
    print(f"[SERVER] Běží na portu {port}")
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
