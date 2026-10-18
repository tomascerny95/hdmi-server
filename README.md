# rpi-tv-remote

A lightweight, ultra-responsive web-based TV remote control for Raspberry Pi (compatible with Wayland and PipeWire). Turn your smartphone, tablet, or secondary PC into a seamless touchpad, on-screen keyboard, HDMI display switch, and audio manager.

GitHub Repository: [https://github.com/tomascerny95/rpi-tv-remote](https://github.com/tomascerny95/rpi-tv-remote)

---

## Features

- **Touchpad Navigation**: Smooth relative mouse pointer (`EV_REL`) with zero lag.
- **Two-Finger Scroll**: Vertical scrolling (mouse wheel simulation) with two-finger swipe or physical PC mouse wheel.
- **Drag & Drop**: Double-tap and drag, or hold down the Left Button to drag windows and files.
- **On-Screen Keyboard**: Full typing with diacritics (`wtype`), QWERTZ / Standard layout compatibility, Esc, Tab, Backspace, Enter, Spacebar, and Navigation Arrows (← ↑ ↓ →).
- **HDMI Power Management**: Easily toggle HDMI TV/monitor power on and off via DRM sysfs.
- **PipeWire Audio Recovery**: Automatically routes sound to HDMI and migrates active media streams (e.g., VLC) to HDMI after waking up the TV.
- **100% Dynamic Setup**: Zero hardcoded usernames or UIDs. Works on any user account out of the box.

---

## Quick Installation & Updates

### 1. Initial Installation
Clone this repository to your Raspberry Pi and execute the setup script:

```bash
git clone https://github.com/tomascerny95/rpi-tv-remote.git
cd rpi-tv-remote
chmod +x setup_hdmi_server.sh
sudo ./setup_hdmi_server.sh
```

Open your browser on your phone or PC:
```text
http://<YOUR_PI_IP>:1111
```

### 2. One-Line Clean Update Command
To pull the latest changes, cleanly reset your working branch, and execute the installer in a single command:

```bash
git fetch origin && git reset --hard origin/main && sudo bash ./setup_hdmi_server.sh
```

---

## Recommended System & VLC Configuration

To prevent HDMI audio dropouts, periodic muting, or buffer desynchronization during media playback, apply the following two tweaks:

### A. Disable HDMI Audio Idle Suspension (PipeWire / WirePlumber)
By default, Raspberry Pi OS suspends idle ALSA sinks after a few seconds of silence, causing TVs to lose HDMI audio synchronization. Disable it with:

```bash
mkdir -p ~/.config/wireplumber/wireplumber.conf.d/
cat << 'EOF' > ~/.config/wireplumber/wireplumber.conf.d/51-disable-suspension.conf
monitor.alsa.rules = [
  {
    matches = [
      {
        node.name = "~alsa_output.*"
      }
    ]
    actions = {
      update-props = {
        session.suspend-timeout-seconds = 0
      }
    }
  }
]
EOF
systemctl --user restart wireplumber
```

### B. Configure VLC Media Player
1. Open VLC on your Raspberry Pi: **Tools → Preferences** (`Ctrl + P`).
2. Go to **Audio** settings:
   - Change **Output module** from *Automatic* to: **PulseAudio audio output**.
3. At the bottom-left, change **Show settings** from *Simple* to **All**.
4. In the left panel, navigate to **Input / Codecs**:
   - Scroll down to the **Advanced** section.
   - Set **File caching (ms)** to: `1000`.
   - Set **Network caching (ms)** to: `1500`.
5. Click **Save** and restart VLC.

---

## Service Management

The remote control server runs as a systemd service (`hdmi-server.service`):

- **Status check**:
  ```bash
  sudo systemctl status hdmi-server.service
  ```
- **Live log output**:
  ```bash
  sudo journalctl -u hdmi-server.service -f
  ```
- **Restart service**:
  ```bash
  sudo systemctl restart hdmi-server.service
  ```
