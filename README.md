# Android Phone Dashboard

A local, read-only dashboard for basic Android device diagnostics over Android Debug Bridge (ADB).

## Requirements

- Python 3
- Android SDK Platform Tools (`adb`) installed and available on `PATH`
- An Android phone connected over USB

## Start

1. On the phone, enable **Developer options** and **USB debugging**.
2. Connect the phone with a USB data cable and approve the USB debugging prompt.
3. Double-click `start-dashboard.bat`.
4. Open the URL printed in the console: `http://127.0.0.1:8765`.
5. Press **Refresh** to update the displayed details. Close the console window to stop the server.

The server binds to localhost only. It displays selected model/build properties, battery state, internal storage usage, screen metrics, uptime, and Android-declared hardware/sensor features. It does not read personal files, messages, contacts, account details, network identifiers, or live sensor readings.

If no phone appears, check that `adb` is on `PATH`, the cable supports data, USB debugging is enabled, and the phone has authorized this computer.
