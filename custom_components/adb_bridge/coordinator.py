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
                        pass
                
                if self.connection_type == CONNECTION_USB:
                    from adb_shell.adb_device import AdbDeviceUsb
                    if self.device_serial:
                        self._device = AdbDeviceUsb(serial=self.device_serial)
                    else:
                        self._device = AdbDeviceUsb()
                else:
                    from adb_shell.adb_device import AdbDeviceTcp
                    self._device = AdbDeviceTcp(self.device_ip, self.adb_port)
                
                self._device.connect(rsa_keys=[self._signer], auth_timeout_s=30)
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
                if not await self._async_connect():
                    raise UpdateFailed("Could not connect to device")
            
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
                            data["wifi_ip"] = result.strip()
                    except Exception:
                        pass
                    
                    # Check if WiFi ADB is enabled
                    try:
                        # Prefer runtime service property
                        result = self._device.shell("getprop service.adb.tcp.port")
                        prop_val = result.strip() if result else ""
                        if not prop_val or prop_val in ("0", "-1"):
                            # Fallback to persisted property used on some devices
                            result = self._device.shell("getprop persist.adb.tcp.port")
                            prop_val = result.strip() if result else ""
                        if prop_val and prop_val not in ("0", "-1"):
                            data["wifi_adb_enabled"] = True
                            try:
                                data["adb_port"] = int(prop_val)
                            except Exception:
                                data["adb_port"] = 5555
                    except Exception:
                        pass
                    
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
        """Enable WiFi ADB on the device. Returns device IP if successful."""
        async with self._lock:
            if self._device is None or not self._device.available:
                if not await self._async_connect():
                    return None
            
            def _enable():
                try:
                    # Capture IP before restarting adbd
                    ip = None
                    result = self._device.shell("ip addr show wlan0 2>/dev/null | grep 'inet ' | awk '{print $2}' | cut -d/ -f1")
                    if result and result.strip():
                        ip = result.strip()
                    else:
                        # Try eth0 as a fallback
                        result = self._device.shell("ip addr show eth0 2>/dev/null | grep 'inet ' | awk '{print $2}' | cut -d/ -f1")
                        if result and result.strip():
                            ip = result.strip()

                    # Attempt to switch ADB to TCP/IP mode and restart adbd in a single shell
                    # Using ctl.restart avoids the problem where a second shell call can't run after stop
                    # Some devices may not allow setting persist.*; best-effort only
                    cmd = (
                        f"setprop service.adb.tcp.port {port}; "
                        f"setprop persist.adb.tcp.port {port}; "
                        f"setprop ctl.restart adbd"
                    )
                    try:
                        self._device.shell(cmd)
                    except Exception as e:
                        _LOGGER.warning("Primary restart path failed, trying fallback: %s", e)
                        # Fallback: try stop/start in the same shell invocation (may still succeed on some builds)
                        try:
                            self._device.shell(
                                f"setprop service.adb.tcp.port {port}; stop adbd; start adbd"
                            )
                        except Exception as e2:
                            _LOGGER.error("Error enabling WiFi ADB (fallback failed): %s", e2)
                            # Even if restart failed, return captured IP if we have it so user can manually act
                            return ip

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
