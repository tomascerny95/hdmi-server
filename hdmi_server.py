#!/usr/bin/env python3
"""
HDMI Controller Server
Lightweight HTTP server for controlling HDMI output on Linux / Raspberry Pi.
"""

import argparse
import logging
import os
import subprocess
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

DEFAULT_PORT = 1111
DEFAULT_DEVICE = "/sys/class/drm/card0-HDMI-A-1/status"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

HTML_PAGE = """<!DOCTYPE html>
<html lang="cs">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HDMI Ovladač</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            text-align: center;
            background-color: #1e1e1e;
            color: #ffffff;
            padding-top: 50px;
            margin: 0;
        }
        h1 { color: #f1f1f1; }
        .btn {
            display: inline-block;
            width: 200px;
            padding: 20px;
            margin: 15px;
            font-size: 18px;
            font-weight: bold;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            transition: 0.3s;
            color: white;
            text-decoration: none;
        }
        .btn-on { background-color: #2ec4b6; }
        .btn-on:hover { background-color: #20a497; }
        .btn-off { background-color: #e71d36; }
        .btn-off:hover { background-color: #b61225; }
    </style>
</head>
<body>
    <h1>Ovládání HDMI Výstupu</h1>
    <p>Vyberte akci pro monitor:</p>
    <a href="/hdmi/on" class="btn btn-on">ZAPNOUT HDMI</a>
    <a href="/hdmi/off" class="btn btn-off">VYPNOUT HDMI</a>
</body>
</html>
"""


class HdmiController:
    def __init__(self, device_path: str):
        self.device_path = device_path

    def set_status(self, action: str) -> bool:
        if action not in ("on", "off"):
            return False

        logging.info(f"Nastavuji HDMI status -> {action}")
        try:
            with open(self.device_path, "w") as f:
                f.write(f"{action}\n")
            return True
        except PermissionError:
            pass
        except Exception as e:
            logging.warning(f"Zápis selhal: {e}")

        cmd = f"echo {action} | sudo tee {self.device_path} > /dev/null"
        try:
            subprocess.run(cmd, shell=True, check=True)
            return True
        except subprocess.SubprocessError as e:
            logging.error(f"Příkaz selhal: {e}")
            return False


class HdmiRequestHandler(BaseHTTPRequestHandler):
    controller: HdmiController = None

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/":
            body = HTML_PAGE.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=UTF-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path in ("/hdmi/on", "/hdmi/off"):
            action = "on" if path == "/hdmi/on" else "off"
            self.controller.set_status(action)
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format, *args):
        logging.info("%s - %s", self.address_string(), format % args)


def main():
    parser = argparse.ArgumentParser(description="HDMI Web Controller")
    parser.add_argument("--port", "-p", type=int, default=DEFAULT_PORT, help="Port serveru")
    parser.add_argument("--device", "-d", type=str, default=DEFAULT_DEVICE, help="Cesta k DRM souboru")
    parser.add_argument("--init-off", action="store_true", help="Vypnout HDMI ihned při spuštění")
    args = parser.parse_args()

    controller = HdmiController(args.device)
    HdmiRequestHandler.controller = controller

    if args.init_off:
        controller.set_status("off")

    server = ThreadingHTTPServer(("0.0.0.0", args.port), HdmiRequestHandler)
    logging.info(f"HDMI server běží na http://0.0.0.0:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
