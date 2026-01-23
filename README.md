# TP-Link Kasa Cloud for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

A Home Assistant custom integration to control TP-Link Kasa devices via the official TP-Link Cloud API.

This integration is particularly useful if:
- Your devices are on a different subnet or VLAN from your Home Assistant instance.
- Local discovery is unreliable on your network.
- You want to control your devices even when local communication is restricted.

## Features
- **Cloud Control**: No need for local network access to the devices.
- **Auto-Discovery**: Automatically finds all devices linked to your Kasa account.
- **Multiple Device Types**: Supports Plugs, Switches, Strips, Bulbs, and Dimmers.
- **Energy Monitoring**: Real-time power usage monitoring (emeter) for supported hardware (e.g., KP115, HS110, EP25).
- **Motion Sensing**: Supports motion detection status and toggling for ES20M switches.
- **Device Management**: Includes buttons for rebooting and toggling the device LED.

## Installation

### Method 1: HACS (Recommended)
1. Ensure [HACS](https://hacs.xyz/) is installed and configured in your Home Assistant.
2. Go to **HACS** -> **Integrations**.
3. Click the three dots `...` in the top right corner and select **Custom repositories**.
4. Paste the URL of this GitHub repository into the **Repository** field.
5. Select **Integration** in the **Category** dropdown.
6. Click **Add**.
7. Click the newly added **TP-Link Kasa Cloud** integration.
8. Click **Download** and then **Download** again in the popup.
9. **Restart Home Assistant** to load the integration.

### Method 2: Manual Installation
1. Download the [latest release](https://github.com/onoffautomations/hassio_kasa_cloud/releases) or the source code.
2. Copy the `custom_components/kasa_cloud` directory into your Home Assistant's `custom_components` directory.
3. **Restart Home Assistant**.

## Configuration
1. In Home Assistant, go to **Settings** -> **Devices & Services**.
2. Click **Add Integration** in the bottom right.
3. Search for **TP-Link Kasa Cloud**.
4. Enter your Kasa/TP-Link username (email) and password.
5. All compatible devices in your account will be added automatically.

## Supported Devices
The integration attempts to support all Kasa devices that are available via the cloud API.
- **Plugs & Power Strips**: HS100, HS103, HS105, HS110, KP115, KP303, KP400, etc.
- **Switches & Dimmers**: HS200, HS210, HS220, ES20M, etc.
- **Bulbs & Light Strips**: KL110, KL125, KL130, KL430, etc.

*Note: Newer Tapo-branded devices or Matter-only devices might not be fully supported if they use different cloud protocols.*

## Troubleshooting
If you encounter issues:
- Check the [Home Assistant Logs](https://www.home-assistant.io/docs/configuration/troubleshooting/#checking-the-logs) for any errors regarding `kasa_cloud`.
- Ensure your credentials are correct and you can log in to the Kasa mobile app.
- If a device is missing, ensure it is enabled for "Remote Control" in the Kasa app.

## Contributing
Feel free to open issues or pull requests if you find a bug or have a feature request!
