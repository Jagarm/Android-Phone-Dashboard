#!/usr/bin/env python3
"""Serve a local, read-only Android diagnostics dashboard using ADB."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
PORT = 8765
PROPERTIES = {
    "Manufacturer": "ro.product.manufacturer",
    "Model": "ro.product.model",
    "Device": "ro.product.device",
    "Android version": "ro.build.version.release",
    "SDK level": "ro.build.version.sdk",
    "Security patch": "ro.build.version.security_patch",
    "Build ID": "ro.build.display.id",
}


def run_adb(serial: str | None, *args: str) -> str:
    command = ["adb"]
    if serial:
        command.extend(["-s", serial])
    command.extend(args)
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )
    if result.returncode:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(message or f"ADB exited with status {result.returncode}")
    return result.stdout.strip()


def connected_devices() -> list[dict[str, str]]:
    output = run_adb(None, "devices", "-l")
    devices = []
    for line in output.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 2:
            devices.append({"serial": fields[0], "state": fields[1]})
    return devices


def property_value(serial: str, prop: str) -> str:
    return run_adb(serial, "shell", "getprop", prop)


def battery_details(serial: str) -> dict[str, str]:
    output = run_adb(serial, "shell", "dumpsys", "battery")
    labels = {
        "level": "Level (%)",
        "status": "Status",
        "health": "Health",
        "plugged": "Power source",
        "temperature": "Temperature (°C)",
        "voltage": "Voltage (mV)",
    }
    values = {}
    for line in output.splitlines():
        match = re.match(r"\s*([A-Za-z ]+):\s*(-?\d+)\s*$", line)
        if match:
            key = match.group(1).strip().lower()
            if key in labels:
                value = match.group(2)
                if key == "temperature":
                    value = f"{int(value) / 10:.1f}"
                values[labels[key]] = value
    status_names = {"1": "Unknown", "2": "Charging", "3": "Discharging", "4": "Not charging", "5": "Full"}
    health_names = {"1": "Unknown", "2": "Good", "3": "Overheat", "4": "Dead", "5": "Over voltage", "6": "Unspecified failure", "7": "Cold"}
    source_names = {"0": "Battery", "1": "AC", "2": "USB", "4": "Wireless"}
    if "Status" in values:
        values["Status"] = status_names.get(values["Status"], values["Status"])
    if "Health" in values:
        values["Health"] = health_names.get(values["Health"], values["Health"])
    if "Power source" in values:
        values["Power source"] = source_names.get(values["Power source"], values["Power source"])
    return values


def storage_details(serial: str) -> dict[str, str]:
    output = run_adb(serial, "shell", "df", "-k", "/data")
    lines = [line.split() for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        raise RuntimeError("Could not parse Android storage information")
    row = next((fields for fields in reversed(lines[1:]) if len(fields) >= 4), None)
    if row is None:
        raise RuntimeError("Could not parse Android storage information")
    total, used, available = (int(value) * 1024 for value in row[1:4])

    def gib(value: int) -> str:
        return f"{value / (1024 ** 3):.1f} GB"

    return {
        "Total": gib(total),
        "Used": gib(used),
        "Available": gib(available),
    }


def device_report(serial: str) -> dict[str, object]:
    info: dict[str, object] = {}
    errors = []
    for label, prop in PROPERTIES.items():
        try:
            info[label] = property_value(serial, prop) or "Not reported"
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{label}: {exc}")

    checks = [
        ("Battery", battery_details),
        ("Internal storage (/data)", storage_details),
    ]
    for label, check in checks:
        try:
            info[label] = check(serial)
        except (RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
            errors.append(f"{label}: {exc}")

    for label, args in (
        ("Screen resolution", ("shell", "wm", "size")),
        ("Screen density", ("shell", "wm", "density")),
        ("Uptime", ("shell", "uptime")),
    ):
        try:
            info[label] = run_adb(serial, *args) or "Not reported"
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{label}: {exc}")

    try:
        feature_output = run_adb(serial, "shell", "pm", "list", "features")
        info["Declared hardware features"] = sorted(
            line.removeprefix("feature:").strip()
            for line in feature_output.splitlines()
            if line.startswith("feature:") and line.removeprefix("feature:").strip()
        )
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        errors.append(f"Hardware features: {exc}")

    return {"info": info, "errors": errors}


class DashboardHandler(BaseHTTPRequestHandler):
    def send_bytes(self, status: int, content_type: str, data: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_bytes(200, "text/html; charset=utf-8", (ROOT / "index.html").read_bytes())
            return
        if parsed.path != "/api/status":
            self.send_bytes(404, "application/json; charset=utf-8", b'{"error":"Not found"}')
            return
        if not shutil.which("adb"):
            payload = {"error": "ADB was not found. Install Android Platform Tools and add adb to PATH."}
            self.send_json(503, payload)
            return
        try:
            devices = connected_devices()
            authorized = [device for device in devices if device["state"] == "device"]
            requested = parse_qs(parsed.query).get("serial", [None])[0]
            authorized_serials = {device["serial"] for device in authorized}
            selected = (
                requested
                if requested in authorized_serials
                else authorized[0]["serial"]
                if len(authorized) == 1
                else None
            )
            report = device_report(selected) if selected else None
            self.send_json(200, {
                "devices": devices,
                "selected": selected,
                "report": report,
            })
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            self.send_json(502, {"error": f"Could not read device status through ADB: {exc}"})

    def send_json(self, status: int, payload: object) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_bytes(status, "application/json; charset=utf-8", data)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    print(f"Android Phone Dashboard: http://127.0.0.1:{PORT}")
    print("This server listens on localhost only. Press Ctrl+C to stop.")
    HTTPServer(("127.0.0.1", PORT), DashboardHandler).serve_forever()
