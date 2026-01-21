# ADB Bridge for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)

Home Assistant integration for managing USB and wireless ADB connections to Android devices.

## Features

- 🔌 **USB ADB Connection** - Detect and connect to USB-attached Android devices
- 📡 **WiFi ADB Management** - Enable wireless ADB with one click
- 📊 **Status Sensors** - Monitor connection status, device IP, and ADB port
- 🎮 **Remote Control** - Run shell commands and install APKs remotely

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=phurth&repository=hacs-adb-connector&category=integration)

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to "Integrations"
3. Click the three dots menu (⋮) → "Custom repositories"
4. Add repository: `https://github.com/phurth/hacs-adb-connector`
5. Category: "Integration"
6. Click "Install"
7. Restart Home Assistant

### Manual

1. Download the `custom_components/adb_bridge` folder
2. Copy to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant

## Configuration

1. **Connect device via USB** to your Home Assistant host
2. Go to **Settings** → **Devices & Services**
3. Click **"+ Add Integration"**
4. Search for **"ADB Bridge"**
5. Select your device from the list
6. **Watch your device screen** for the "Allow USB debugging?" prompt
7. Check "Always allow from this computer" and tap "OK"

### Requirements

- USB debugging enabled on Android device
- USB cable connected to Home Assistant host
- Device appears in `/dev/bus/usb/` (automatic with Proxmox USB passthrough or bare metal)

## Entities

Once configured, you'll get the following entities:

| Entity | Description |
|--------|-------------|
| `sensor.<device>_connection_status` | Shows "Connected" or "Disconnected" |
| `sensor.<device>_wifi_ip_address` | Device's WiFi IP address |
| `sensor.<device>_adb_port` | ADB port number (shown when WiFi ADB is enabled) |
| `button.<device>_enable_wifi_adb` | One-click enable wireless ADB |
| `button.<device>_reconnect` | Force reconnection to device |

## Usage

### Enable Wireless ADB

1. Connect device via USB initially
2. Press the **"Enable WiFi ADB"** button
3. Device becomes accessible wirelessly at `<ip>:5555`
4. USB cable can now be disconnected

The device will remain in wireless mode until rebooted.

### Connect from Computer

Once wireless ADB is enabled, connect from any computer on your network:

```bash
adb connect <device_ip>:5555
adb devices
adb install myapp.apk
```

Use scrcpy for remote screen access:

```bash
scrcpy -s <device_ip>:5555
```

### Automation Example

Automatically enable WiFi ADB when device connects:

```yaml
automation:
  - alias: "Enable WiFi ADB on connect"
    trigger:
      - platform: state
        entity_id: sensor.adb_device_connection_status
        to: "Connected"
    condition:
      - condition: state
        entity_id: sensor.adb_device_wifi_ip_address
        state: "unknown"
    action:
      - service: button.press
        target:
          entity_id: button.adb_device_enable_wifi_adb
```

## Troubleshooting

### USB device not detected

- Ensure USB debugging is enabled on Android device
- Check USB cable supports data transfer (not charge-only)
- Try a different USB port
- Verify device appears in `ls -la /dev/bus/usb/` from HA terminal

### Connection fails immediately

- Check HA logs: Settings → System → Logs → Search "adb_bridge"
- Ensure no other ADB server is connected to the device
- On device, revoke USB debugging authorizations and try again:
  Settings → Developer Options → Revoke USB debugging authorizations

### WiFi ADB not working

- Verify device is on same network as Home Assistant
- Check firewall isn't blocking port 5555
- Some devices require WiFi to be enabled first (not Ethernet)

## Development

This integration uses the [adb-shell](https://github.com/JeffLIrion/adb_shell) library for ADB communication.

### Requirements

- Home Assistant 2024.1.0 or newer
- Python 3.11+
- Android device with USB debugging enabled

## License

MIT License - see LICENSE file for details

## Credits

- Built for managing headless Android devices in Home Assistant
- Uses [adb-shell](https://github.com/JeffLIrion/adb_shell) for ADB protocol implementation
- Inspired by the Home Assistant [Android TV](https://www.home-assistant.io/integrations/androidtv/) integration
