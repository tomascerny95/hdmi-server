# HDMI Web Controller

A lightweight, headless HDMI controller server optimized for Raspberry Pi and Linux, designed to remotely manage HDMI display power states directly via a web browser.

---

## Overview

HDMI Web Controller provides a minimalist web dashboard to power on and off HDMI outputs on Linux systems. It acts as an ultra-low-footprint remote switch for TVs and monitors connected to a Raspberry Pi or home server, completely avoiding heavy desktop environments, complex dependencies, or background databases.

### Key Components

- **Web Dashboard (Port 1111):** Pure Python HTTP service delivering an instant responsive web interface to control display status.
- **Direct DRM Sysfs Control:** Communicates directly with the Linux kernel Direct Rendering Manager (`/sys/class/drm/card0-HDMI-A-1/status`) without relying on X11 or Wayland display servers.
- **Systemd Background Service:** Runs directly in-place from your cloned repository directory with automatic restart on boot.

---

## Features

- **Zero External Dependencies:** Built entirely with standard Python 3 (`http.server`, `subprocess`) — no `pip`, Flask, or external libraries required.
- **Run In-Place:** Runs directly from your user directory (e.g. `/home/cernytom/hdmi-server`) without moving files to system paths.
- **Auto-User Detection:** Installation script automatically detects the calling user and working directory, configuring systemd dynamically.
- **Direct Kernel Level Control:** Toggles display power via `/sys/class/drm/.../status`, ensuring compatibility even in headless setups.
- **Instant Response & Clean Redirects:** Action endpoints automatically redirect back to the control dashboard via HTTP 303.
- **Minimal Footprint:** Uses less than 15 MB of RAM and near 0% CPU, making it ideal for 24/7 Raspberry Pi devices.

---

## Architecture & Ports

| Service | Port / Path | Description |
| :--- | :--- | :--- |
| **HDMI Web Server** | `1111` | Responsive web dashboard and control endpoints |
| **Hardware Interface** | `/sys/class/drm/card0-HDMI-A-1/status` | Linux kernel DRM sysfs interface for HDMI output |

---

## Prerequisites

- Debian / Ubuntu / Raspberry Pi OS
- Python 3.8+
- Sudo privileges for running the installer

---

## Quick Installation

1. **Clone the repository:**
```bash
git clone https://github.com/tomascerny95/hdmi-server.git
cd hdmi-server
```

2. **Run the automated setup script:**
```bash
chmod +x setup_hdmi_server.sh
sudo ./setup_hdmi_server.sh
```

3. **Access the web interface:**
   - **HDMI Dashboard:** `http://<YOUR_PI_IP>:1111`

---

## Updating

To pull the latest updates from GitHub and automatically reconfigure the service:

```bash
git fetch origin && git reset --hard origin/main && sudo bash ./setup_hdmi_server.sh
```

---

## Manual Execution

You can also run the server directly from the folder without systemd:

```bash
# Run with default port (1111)
sudo python3 hdmi_server.py

# Run on a custom port
sudo python3 hdmi_server.py --port 8080

# Turn HDMI off immediately on server start
sudo python3 hdmi_server.py --init-off
```

---

## Project Structure

```text
hdmi-server/
├── hdmi_server.py           # Core Python HTTP server and DRM controller
├── setup_hdmi_server.sh     # Automated installation and systemd deployment script
├── README.md                # Project documentation
└── LICENSE                  # Open source license
```

---

## Endpoints

- `GET /` - Main web dashboard with interactive HDMI ON / OFF buttons.
- `GET /hdmi/on` - Triggers HDMI display activation and redirects back to `/`.
- `GET /hdmi/off` - Powers down the HDMI display output and redirects back to `/`.

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
