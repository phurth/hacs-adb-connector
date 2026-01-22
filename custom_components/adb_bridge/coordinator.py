"""DataUpdateCoordinator for ADB Bridge."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_CONNECTION_TYPE,
    CONF_DEVICE_SERIAL,
    CONF_DEVICE_IP,
    CONF_ADB_PORT,
    CONNECTION_USB,
    CONNECTION_WIFI,
    DEFAULT_ADB_PORT,
)

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(seconds=30)


class AdbBridgeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to manage ADB device connection."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.entry = entry
        self._device = None
        self._signer = None
        self._lock = asyncio.Lock()
        self._last_wifi_ip: str | None = None
        self._last_wifi_port: int = DEFAULT_ADB_PORT
        
        self.connection_type = entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_USB)
        self.device_serial = entry.data.get(CONF_DEVICE_SERIAL)
        self.device_ip = entry.data.get(CONF_DEVICE_IP)
        self.adb_port = entry.data.get(CONF_ADB_PORT, DEFAULT_ADB_PORT)

    async def _async_setup(self) -> None:
        """Set up the ADB connection."""
        def _setup():
            from adb_shell.auth.keygen import keygen
            from adb_shell.auth.sign_pythonrsa import PythonRSASigner
            
            key_path = "/config/.android/adbkey"
            os.makedirs(os.path.dirname(key_path), exist_ok=True)
            
            if not os.path.exists(key_path):
                keygen(key_path)
            
            with open(key_path) as f:
                priv = f.read()
            with open(key_path + ".pub") as f:
                pub = f.read()
            
            self._signer = PythonRSASigner(pub, priv)
        
        await self.hass.async_add_executor_job(_setup)

    async def _async_connect(self) -> bool:
        """Connect to the device."""
        if self._signer is None:
            await self._async_setup()
        
        def _connect():
            try:
                # Don't reconnect if already connected and working
                if self._device and self._device.available:
                    try:
                        # Test the connection
                        self._device.shell("echo test")
                        return True
                    except Exception:
                        # Connection is stale, reconnect
                        _LOGGER.debug("Existing connection stale, reconnecting...")
                        try:
                            self._device.close()
                        except Exception:
                            pass
                        self._device = None
                
                if self.connection_type == CONNECTION_USB:
                    from adb_shell.adb_device import AdbDeviceUsb
                    import usb.core
                    
                    # Debug: enumerate what USB devices we can see
                    try:
                        all_usb = list(usb.core.find(find_all=True))
                        android_devs = [d for d in all_usb if d.idVendor == 0x18d1]
                        _LOGGER.info("USB scan: %d total devices, %d Google/Android devices", len(all_usb), len(android_devs))
                        for d in android_devs:
                            _LOGGER.info("  Found: %04x:%04x serial=%s", d.idVendor, d.idProduct, getattr(d, 'serial_number', 'N/A'))
                    except Exception as e:
                        _LOGGER.warning("USB enumeration failed: %s", e)
                    
                    _LOGGER.debug("Connecting via USB (serial=%s)", self.device_serial)
                    if self.device_serial:
                        self._device = AdbDeviceUsb(serial=self.device_serial)
                    else:
                        self._device = AdbDeviceUsb()
                else:
                    from adb_shell.adb_device import AdbDeviceTcp
                    _LOGGER.debug("Connecting via TCP to %s:%s", self.device_ip, self.adb_port)
                    self._device = AdbDeviceTcp(self.device_ip, self.adb_port)
                
                self._device.connect(rsa_keys=[self._signer], auth_timeout_s=30)
                _LOGGER.info("ADB connected successfully (available=%s)", self._device.available)
                return self._device.available
            except Exception as e:
                _LOGGER.error("Connection failed: %s", e)
                self._device = None
                return False
        
        return await self.hass.async_add_executor_job(_connect)

    async def async_disconnect(self) -> None:
        """Disconnect from device."""
        def _disconnect():
            if self._device:
                try:
                    self._device.close()
                except Exception:
                    pass
                self._device = None
        
        await self.hass.async_add_executor_job(_disconnect)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from device."""
        async with self._lock:
            # Don't try to reconnect if we're not supposed to be connected
            if self._device is None:
                # Attempt connection over configured transport
                if not await self._async_connect():
                    # If USB isn't available, still try to validate WiFi ADB based on last-known info
                    def _wifi_probe_from_cache():
                        import socket
                        data = {
                            "connected": False,
                            "serial": None,
                            "wifi_ip": self._last_wifi_ip,
                            "wifi_adb_enabled": False,
                            "adb_port": self._last_wifi_port,
                        }
                        if not self._last_wifi_ip or not self._last_wifi_port:
                            return data
                        try:
                            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            sock.settimeout(2)
                            result = sock.connect_ex((self._last_wifi_ip, self._last_wifi_port))
                            sock.close()
                            data["wifi_adb_enabled"] = (result == 0)
                        except Exception:
                            data["wifi_adb_enabled"] = False
                        return data

                    return await self.hass.async_add_executor_job(_wifi_probe_from_cache)
            
            def _get_data():
                data = {
                    "connected": False,
                    "serial": None,
                    "wifi_ip": None,
                    "wifi_adb_enabled": False,
                    "adb_port": 5555,
                }
                
                try:
                    # Test if connection is still alive
                    if not self._device or not self._device.available:
                        return data
                    
                    # Quick test
                    try:
                        self._device.shell("echo test")
                    except Exception:
                        # Connection is dead
                        self._device = None
                        return data
                    
                    data["connected"] = True
                    
                    # Get serial
                    try:
                        result = self._device.shell("getprop ro.serialno")
                        data["serial"] = result.strip() if result else self.device_serial
                    except Exception:
                        data["serial"] = self.device_serial
                    
                    # Get WiFi IP
                    try:
                        result = self._device.shell("ip addr show wlan0 2>/dev/null | grep 'inet ' | awk '{print $2}' | cut -d/ -f1")
                        if result and result.strip():
                            ip_val = result.strip()
                            data["wifi_ip"] = ip_val
                            self._last_wifi_ip = ip_val
                        else:
                            # Try eth0 as fallback
                            result = self._device.shell("ip addr show eth0 2>/dev/null | grep 'inet ' | awk '{print $2}' | cut -d/ -f1")
                            if result and result.strip():
                                ip_val = result.strip()
                                data["wifi_ip"] = ip_val
                                self._last_wifi_ip = ip_val
                    except Exception:
                        pass
                    
                    # Check if WiFi ADB is enabled by checking if port is listening
                    # We use a simple socket connect instead of full ADB handshake
                    # because ADB might reject a second connection while USB is active
                    port_to_check = self._last_wifi_port or 5555
                    data["adb_port"] = port_to_check
                    
                    if data.get("wifi_ip"):
                        import socket
                        try:
                            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            sock.settimeout(2)
                            result = sock.connect_ex((data["wifi_ip"], port_to_check))
                            sock.close()
                            data["wifi_adb_enabled"] = (result == 0)
                            _LOGGER.debug("WiFi ADB port check: %s:%s = %s (result=%s)", 
                                         data["wifi_ip"], port_to_check, data["wifi_adb_enabled"], result)
                        except Exception as e:
                            _LOGGER.debug("WiFi ADB port check failed: %s:%s - %s", data["wifi_ip"], port_to_check, e)
                            data["wifi_adb_enabled"] = False
                    
                    return data
                except Exception as e:
                    _LOGGER.error("Error getting device data: %s", e)
                    self._device = None
                    raise
            
            try:
                return await self.hass.async_add_executor_job(_get_data)
            except Exception as e:
                raise UpdateFailed(f"Error communicating with device: {e}")

    async def async_enable_wifi_adb(self, port: int = 5555) -> str | None:
        """Enable WiFi ADB on the device. Returns device IP if successful.
        
        Uses the ADB protocol's tcpip service (equivalent to `adb tcpip <port>`)
        which works without root, unlike shell-based setprop approaches.
        """
        async with self._lock:
            if self._device is None or not self._device.available:
                if not await self._async_connect():
                    _LOGGER.error("Cannot enable WiFi ADB: device not connected")
                    return None
            
            def _enable():
                try:
                    # Capture IP before sending tcpip command (adbd will restart)
                    ip = None
                    try:
                        result = self._device.shell("ip addr show wlan0 2>/dev/null | grep 'inet ' | awk '{print $2}' | cut -d/ -f1")
                        if result and result.strip():
                            ip = result.strip()
                        else:
                            result = self._device.shell("ip addr show eth0 2>/dev/null | grep 'inet ' | awk '{print $2}' | cut -d/ -f1")
                            if result and result.strip():
                                ip = result.strip()
                    except Exception as e:
                        _LOGGER.warning("Could not get device IP: %s", e)

                    _LOGGER.info("Enabling WiFi ADB on port %s (device IP: %s)", port, ip)

                    # Use the ADB protocol tcpip service - this is what `adb tcpip <port>` does
                    # It tells adbd to restart listening on the specified TCP port
                    # This works WITHOUT root, unlike setprop approaches
                    try:
                        # _service() is the internal method used by reboot() etc.
                        response = self._device._service(b'tcpip', str(port).encode('utf-8'), timeout_s=10)
                        _LOGGER.info("tcpip service response: %s", response)
                    except Exception as e:
                        _LOGGER.warning("ADB tcpip service failed: %s - trying shell fallback", e)
                        # Fallback for older devices or unusual configurations
                        try:
                            self._device.shell(
                                f"setprop service.adb.tcp.port {port}; "
                                f"setprop persist.adb.tcp.port {port}; "
                                f"stop adbd; start adbd"
                            )
                        except Exception as e2:
                            _LOGGER.error("Shell fallback also failed: %s", e2)

                    # Cache IP/port for state tracking
                    if ip:
                        self._last_wifi_ip = ip
                        self._last_wifi_port = port

                    return ip
                except Exception as e:
                    _LOGGER.error("Error enabling WiFi ADB: %s", e)
                    return None
            
            return await self.hass.async_add_executor_job(_enable)

    async def async_run_command(self, command: str) -> str | None:
        """Run a shell command on the device."""
        async with self._lock:
            if self._device is None or not self._device.available:
                if not await self._async_connect():
                    return None
            
            def _run():
                try:
                    return self._device.shell(command)
                except Exception as e:
                    _LOGGER.error("Error running command: %s", e)
                    return None
            
            return await self.hass.async_add_executor_job(_run)

    async def async_install_apk(self, apk_path: str) -> bool:
        """Install an APK on the device."""
        async with self._lock:
            if self._device is None or not self._device.available:
                if not await self._async_connect():
                    return False
            
            def _install():
                try:
                    from adb_shell.adb_device import AdbDevice
                    # Push APK to device
                    remote_path = "/data/local/tmp/install.apk"
                    self._device.push(apk_path, remote_path)
                    
                    # Install
                    result = self._device.shell(f"pm install -r {remote_path}")
                    
                    # Clean up
                    self._device.shell(f"rm {remote_path}")
                    
                    return "Success" in result if result else False
                except Exception as e:
                    _LOGGER.error("Error installing APK: %s", e)
                    return False
            
            return await self.hass.async_add_executor_job(_install)
