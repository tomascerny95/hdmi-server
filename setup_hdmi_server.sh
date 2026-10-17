#!/usr/bin/env bash
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
  echo "[-] Spusťte jako root (sudo ./setup_hdmi_server.sh)"
  exit 1
fi

INSTALL_DIR="/opt/hdmi-server"
SERVICE_NAME="hdmi-server.service"

mkdir -p "$INSTALL_DIR"
cp hdmi_server.py "$INSTALL_DIR/hdmi_server.py"
chmod +x "$INSTALL_DIR/hdmi_server.py"

cat << 'EOF' > "/etc/systemd/system/$SERVICE_NAME"
[Unit]
Description=HDMI Web Controller Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/hdmi-server
ExecStart=/usr/bin/python3 /opt/hdmi-server/hdmi_server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "Hotovo! Server běží na http://${IP:-localhost}:1111"
