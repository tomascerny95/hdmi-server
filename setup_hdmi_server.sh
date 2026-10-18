#!/usr/bin/env bash
# ==============================================================================
# Setup script for rpi-tv-remote (https://github.com/tomascerny95/rpi-tv-remote)
# Dynamically detects user, UID, paths, and configures a systemd service.
# ==============================================================================

set -euo pipefail

# 1. Root check
if [ "$EUID" -ne 0 ]; then
  echo "[-] This script must be run as root (use: sudo ./setup_hdmi_server.sh)"
  exit 1
fi

# 2. Dynamic project directory detection
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_NAME="hdmi_server.py"
SERVICE_NAME="hdmi-server.service"

# 3. Dynamic user & UID detection (handles sudo execution)
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  RUN_USER="$SUDO_USER"
else
  RUN_USER="$(stat -c '%U' "$PROJECT_DIR")"
fi

RUN_UID="$(id -u "$RUN_USER")"

echo "[+] Detected user: $RUN_USER (UID: $RUN_UID)"
echo "[+] Project directory: $PROJECT_DIR"

# 4. Check presence of Python script
if [ ! -f "$PROJECT_DIR/$SCRIPT_NAME" ]; then
  echo "[-] Error: File $SCRIPT_NAME was not found in $PROJECT_DIR"
  exit 1
fi

chmod +x "$PROJECT_DIR/$SCRIPT_NAME"

# 5. Dependency check and installation
echo "[+] Installing required system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-evdev wtype pulseaudio-utils

# Ensure uinput kernel module is persistently loaded across reboots
modprobe uinput || true
if ! grep -qxF "uinput" /etc/modules; then
  echo "uinput" >> /etc/modules
  echo "[+] Module 'uinput' persistently added to /etc/modules"
fi

# Enable user session linger for headless / autostart run
loginctl enable-linger "$RUN_USER" || true

# 6. Generate systemd service (clean dependencies without deadlocks)
echo "[+] Creating systemd service: /etc/systemd/system/$SERVICE_NAME..."
cat << EOF > "/etc/systemd/system/$SERVICE_NAME"
[Unit]
Description=Raspberry Pi TV Remote Controller Server
After=network.target sound.target

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
Environment="RPI_USER=$RUN_USER"
Environment="RPI_UID=$RUN_UID"
ExecStart=/usr/bin/python3 $PROJECT_DIR/$SCRIPT_NAME
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 7. Reload and activate service
echo "[+] Reloading systemd and starting service..."
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -z "$LOCAL_IP" ] && LOCAL_IP="localhost"

echo "========================================================"
echo "[✓] Installation complete and service is running!"
echo "    Project directory: $PROJECT_DIR"
echo "    Service user:      $RUN_USER"
echo "    Remote URL:        http://${LOCAL_IP}:1111"
echo ""
echo "    Management commands:"
echo "      sudo systemctl status $SERVICE_NAME"
echo "      sudo journalctl -u $SERVICE_NAME -f"
echo "========================================================"
