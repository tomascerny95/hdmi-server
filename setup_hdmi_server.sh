#!/usr/bin/env bash
# ==============================================================================
# Setup script for HDMI Controller Server
# Dynamically detects the current user and repository path, then creates systemd service.
# ==============================================================================

set -euo pipefail

# 1. Kontrola root oprávnění
if [ "$EUID" -ne 0 ]; then
  echo "[-] Tento skript musí být spuštěn s právy root (použij: sudo ./setup_hdmi_server.sh)"
  exit 1
fi

# 2. Automatická detekce cesty ke složce s projektem
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_NAME="hdmi_server.py"
SERVICE_NAME="hdmi-server.service"

# 3. Automatická detekce skutečného uživatele a jeho UID (i při spuštění přes sudo)
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  RUN_USER="$SUDO_USER"
else
  RUN_USER="$(stat -c '%U' "$PROJECT_DIR")"
fi

RUN_UID="$(id -u "$RUN_USER")"

echo "[+] Detekován uživatel: $RUN_USER (UID: $RUN_UID)"
echo "[+] Cesta projektu:     $PROJECT_DIR"

# 4. Kontrola přítomnosti Python skriptu
if [ ! -f "$PROJECT_DIR/$SCRIPT_NAME" ]; then
  echo "[-] Chyba: Soubor $SCRIPT_NAME nebyl nalezen v $PROJECT_DIR"
  exit 1
fi

chmod +x "$PROJECT_DIR/$SCRIPT_NAME"

# 5. Kontrola a instalace potřebných balíčků
echo "[+] Kontrola a instalace systémových balíčků..."
apt-get update -qq
apt-get install -y -qq python3 python3-evdev wtype pulseaudio-utils

# Trvalé načtení modulu uinput pro virtuální myš i po restartu
modprobe uinput || true
if ! grep -qxF "uinput" /etc/modules; then
  echo "uinput" >> /etc/modules
  echo "[+] Modul uinput byl trvale přidán do /etc/modules"
fi

# Povolení běhu uživatelských sessions i po startu bez nutnosti přihlášení
loginctl enable-linger "$RUN_USER" || true

# 6. Generování systemd služby (ČISTÁ VERZE BEZ CYKLICKÝCH SMYČEK)
echo "[+] Vytvářím systemd službu: /etc/systemd/system/$SERVICE_NAME..."
cat << EOF > "/etc/systemd/system/$SERVICE_NAME"
[Unit]
Description=HDMI Web Controller Server
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

# 7. Aktivace a spuštění služby
echo "[+] Načítám a spouštím systemd službu..."
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -z "$LOCAL_IP" ] && LOCAL_IP="localhost"

echo "========================================================"
echo "[✓] Instalace dokončena a služba běží!"
echo "    Projekt běží z:  $PROJECT_DIR"
echo "    Uživatel služby: $RUN_USER"
echo "    HDMI Server:     http://${LOCAL_IP}:1111"
echo ""
echo "    Příkazy pro správu:"
echo "      sudo systemctl status $SERVICE_NAME"
echo "      sudo journalctl -u $SERVICE_NAME -f"
echo "========================================================"
