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

# 3. Automatická detekce skutečného uživatele (i při spuštění přes sudo)
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  RUN_USER="$SUDO_USER"
else
  # Fallback: vlastník složky s projektem
  RUN_USER="$(stat -c '%U' "$PROJECT_DIR")"
fi

echo "[+] Detekován uživatel: $RUN_USER"
echo "[+] Cesta projektu:     $PROJECT_DIR"

# 4. Kontrola přítomnosti Python skriptu
if [ ! -f "$PROJECT_DIR/$SCRIPT_NAME" ]; then
  echo "[-] Chyba: Soubor $SCRIPT_NAME nebyl nalezen v $PROJECT_DIR"
  exit 1
fi

chmod +x "$PROJECT_DIR/$SCRIPT_NAME"

# 5. Kontrola Python 3
if ! command -v python3 &> /dev/null; then
  echo "[+] Instaluji python3..."
  apt-get update && apt-get install -y python3
fi

# 6. Generování systemd služby pro běh přímo v projektové složce
echo "[+] Vytvářím systemd službu: /etc/systemd/system/$SERVICE_NAME..."
cat << EOF > "/etc/systemd/system/$SERVICE_NAME"
[Unit]
Description=HDMI Web Controller Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
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
echo "[✓] Instalace dokončena!"
echo "    Projekt běží z:  $PROJECT_DIR"
echo "    HDMI Server:     http://${LOCAL_IP}:1111"
echo ""
echo "    Příkazy pro správu:"
echo "      sudo systemctl status $SERVICE_NAME"
echo "      sudo journalctl -u $SERVICE_NAME -f"
echo "========================================================"
