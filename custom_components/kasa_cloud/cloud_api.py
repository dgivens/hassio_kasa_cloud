"""TP-Link Kasa Cloud API Client."""
import logging
import uuid
import json
import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://wap.tplinkcloud.com"


class KasaCloudDevice:
    """Representation of a Kasa Cloud Device with control capabilities."""
    def __init__(self, device_info: dict, client: 'KasaCloudClient'):
        self.device_info = device_info
        self.client = client
        self.device_id = device_info.get("deviceId")
        self.alias = device_info.get("alias")
        self.device_type = device_info.get("deviceType")
        self.device_model = device_info.get("deviceModel")
        self.device_mac = device_info.get("deviceMac")
        self.hw_id = device_info.get("hwId")
        self.fw_id = device_info.get("fwId")
        self.oem_id = device_info.get("oemId")
        self.app_server_url = device_info.get("appServerUrl")
        self.device_region = device_info.get("deviceRegion")
        
        self.sys_info = {}
        
    def get_alias(self):
        return self.alias

    @property
    def host(self):
        """Mock host for compatibility."""
        return self.device_id

    @property
    def model(self):
        """Return device model."""
        return self.device_model

    @property
    def mac(self):
        """Return device mac."""
        return self.device_mac

    @property
    def is_plug(self):
        """Guess if it's a plug."""
        return "Plug" in self.device_type or "plug" in self.device_type.lower()

    @property
    def is_wall_switch(self):
        """Guess if it's a switch."""
        return "Switch" in self.device_type or "switch" in self.device_type.lower()
        
    @property
    def is_strip(self):
        """Guess if it's a strip."""
        return "Power Strip" in self.device_type or "strip" in self.device_type.lower() or self.has_children

    @property
    def is_bulb(self):
        return "Bulb" in self.device_type or "bulb" in self.device_type.lower() or "IOT.SMARTBULB" in self.device_type
        
    @property
    def is_light_strip(self):
        return "Light Strip" in self.device_type or "strip" in self.device_type.lower() and self.is_bulb
        
    @property
    def is_dimmable(self):
        return self.is_bulb or "dimmer" in self.device_type.lower() or "ES20M" in self.model
        
    @property
    def is_variable_color_temp(self):
        return self.sys_info and "color_temp" in self.sys_info
        
    @property
    def is_color(self):
        return self.sys_info and "hsv" in self.sys_info

    @property
    def has_children(self):
        """Check if device has children plugs."""
        if self.sys_info and "children" in self.sys_info:
            return len(self.sys_info["children"]) > 0
        return False

    @property
    def children(self):
        """Return list of child devices."""
        kids = []
        if self.has_children:
            for child in self.sys_info["children"]:
                kids.append(KasaCloudChildDevice(child, self))
        return kids

    @property
    def status(self):
        """Return online status (1=online, 0=offline)."""
        return self.device_info.get("status", 1)

    @property
    def is_on(self):
        """Check if device is on from cached sys_info."""
        if not self.sys_info:
            return False
        relay_state = self.sys_info.get("relay_state")
        return relay_state == 1

    @property
    def brightness(self):
        """Return brightness 0-255."""
        if not self.sys_info:
            return 0
        # Kasa uses 0-100
        val = self.sys_info.get("brightness", 0)
        return int(val * 255 / 100)

    @property
    def color_temp(self):
        """Return color temp in Kelvin."""
        return self.sys_info.get("color_temp", 2700)

    @property
    def hw_info(self):
        """Return hardware info."""
        return {
            "sw_ver": self.fw_id,
            "hw_ver": self.hw_id,
            "mac": self.mac,
            "model": self.model,
        }

    @property
    def has_emeter(self):
        """Check if device has emeter."""
        # Simplified check. "ENE" in feature string usually
        # But we don't have feature string populated in initial device info often.
        # Check model names known for emeter: KP115, HS110, KP125, EP25, ES20M
        model = self.model.upper() if self.model else ""
        return "110" in model or "115" in model or "125" in model or "25" in model or "ENE" in self.device_info.get("feature", "")

    @property
    def rssi(self):
        """Return RSSI."""
        return self.sys_info.get("rssi")

    @property
    def on_since(self):
        """Return on_since time."""
        return self.sys_info.get("on_time")

    @property
    def overheated(self):
        """Return overheat status."""
        return self.sys_info.get("overheated")

    async def update(self):
        """Update state from cloud."""
        try:
            result = await self.client.passthrough(
                self.device_id, 
                {"system": {"get_sysinfo": {}}}, 
                self.app_server_url
            )
            
            if result.get("error_code") == 0:
                response_data = result.get("result", {}).get("responseData")
                if response_data:
                    data = json.loads(response_data)
                    self.sys_info = data.get("system", {}).get("get_sysinfo", {})
        except Exception as err:
            _LOGGER.error("Error updating device %s: %s", self.alias, err)

    async def turn_on(self):
        """Turn device on."""
        cmd = {"system": {"set_relay_state": {"state": 1}}}
        # For bulbs:
        if self.is_bulb or self.is_dimmable:
             cmd = {"smartlife.iot.smartbulb.lightingservice": {"transition_light_state": {"on_off": 1}}}
        
        await self.client.passthrough(self.device_id, cmd, self.app_server_url)

    async def turn_off(self):
        """Turn device off."""
        cmd = {"system": {"set_relay_state": {"state": 0}}}
        if self.is_bulb or self.is_dimmable:
             cmd = {"smartlife.iot.smartbulb.lightingservice": {"transition_light_state": {"on_off": 0}}}
        await self.client.passthrough(self.device_id, cmd, self.app_server_url)

    async def set_brightness(self, brightness):
        """Set brightness (0-100). Home assistant sends 0-255."""
        kasa_brightness = int(brightness * 100 / 255)
        cmd = {"smartlife.iot.smartbulb.lightingservice": {"transition_light_state": {"brightness": kasa_brightness, "on_off": 1}}}
        await self.client.passthrough(self.device_id, cmd, self.app_server_url)

    async def set_color_temp(self, temp):
        """Set color temp."""
        cmd = {"smartlife.iot.smartbulb.lightingservice": {"transition_light_state": {"color_temp": temp, "on_off": 1}}}
        await self.client.passthrough(self.device_id, cmd, self.app_server_url)

    async def set_hsv(self, h, s, v):
        """Set HSV."""
        cmd = {"smartlife.iot.smartbulb.lightingservice": {"transition_light_state": {"hue": h, "saturation": s, "brightness": v, "color_temp": 0, "on_off": 1}}}
        await self.client.passthrough(self.device_id, cmd, self.app_server_url)

    async def reboot(self):
        """Reboot the device."""
        await self.client.passthrough(self.device_id, {"system": {"reboot": {"delay": 1}}}, self.app_server_url)

    async def set_led(self, state: bool):
        """Set LED on/off."""
        # state True = On, False = Off. Kasa command usually set_led_off 0 (on) or 1 (off)
        off_value = 0 if state else 1
        await self.client.passthrough(self.device_id, {"system": {"set_led_off": {"off": off_value}}}, self.app_server_url)

    async def set_motion_detection(self, enabled: bool):
        """Set motion detection enable/disable."""
        # Common for ES20M
        val = 1 if enabled else 0
        # Try both common services
        cmd = {"smartlife.iot.smartswitch.motion_switch_service": {"set_config": {"motion_enable": val}}}
        await self.client.passthrough(self.device_id, cmd, self.app_server_url)

    @property
    def led_status(self):
        """Return LED status (True=On)."""
        # led_off: 0 means On, 1 means Off
        if self.sys_info and "led_off" in self.sys_info:
             return self.sys_info["led_off"] == 0
        return True # Default to on

    @property
    def motion_enabled(self):
        """Return motion detection enabled status."""
        # Look for motion service config
        if self.sys_info:
             # Check for nested service
             service = self.sys_info.get("smartlife.iot.smartswitch.motion_switch_service")
             if service and "motion_enable" in service:
                  return service["motion_enable"] == 1
             # Check flat
             if "motion_enable" in self.sys_info:
                  return self.sys_info["motion_enable"] == 1
        return False

    @property
    def is_connected(self):
        """Return True if connected to cloud."""
        return self.status == 1


class KasaCloudChildDevice:
    """Representation of a child plug (e.g. on a strip)."""
    def __init__(self, data: dict, parent: KasaCloudDevice):
        self.data = data
        self.parent = parent
        self.device_id = data.get("id")
        self._id = data.get("id")
    
    @property
    def alias(self):
        return self.data.get("alias")
        
    @property
    def is_on(self):
        return self.data.get("state") == 1
        
    async def turn_on(self):
        context = {"child_ids": [self._id]}
        cmd = {"system": {"set_relay_state": {"state": 1}}}
        await self.parent.client.passthrough(self.parent.device_id, cmd, self.parent.app_server_url, context=context)
        
    async def turn_off(self):
        context = {"child_ids": [self._id]}
        cmd = {"system": {"set_relay_state": {"state": 0}}}
        await self.parent.client.passthrough(self.parent.device_id, cmd, self.parent.app_server_url, context=context)

    @property
    def device_info(self):
         info = self.parent.device_info.copy()
         info["deviceId"] = self._id
         info["alias"] = self.alias
         return info

    @property
    def model(self):
        return self.parent.model

    @property
    def hw_info(self):
        return {"sw_ver": self.parent.fw_id}

    # Child devices on strip usually don't support dimming/color/led control individually
    @property
    def is_dimmable(self):
        return False
        
    @property
    def is_bulb(self):
        return False
        
    @property
    def is_light_strip(self):
        return False
        
    @property
    def has_emeter(self):
        return False # Todo check


class KasaCloudClient:
    """Client for TP-Link Kasa Cloud."""

    def __init__(self, username, password):
        self._username = username
        self._password = password
        self._token = None
        self._term_id = str(uuid.uuid4())

    async def _call(self, method: str, params: dict = None, url_override: str = None) -> dict:
        """Make API call to TP-Link cloud."""
        params = params or {}
        
        base = url_override or BASE_URL
        url = f"{base}?termID={self._term_id}"
        
        if self._token:
            url += f"&token={self._token}"
        
        payload = {"method": method, "params": params}
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    response.raise_for_status()
                    return await response.json()
            except Exception as e:
                _LOGGER.error("API call failed: %s", e)
                return {"error_code": -1, "msg": str(e)}

    async def login(self):
        """Login to Kasa Cloud."""
        self._token = None
        
        data = await self._call("login", {
            "appType": "Kasa",
            "cloudUserName": self._username,
            "cloudPassword": self._password,
            "terminalUUID": self._term_id
        })
        
        if data.get("error_code") != 0:
            raise Exception(f"Login failed: {data}")
        
        result = data.get("result", {})
        self._token = result.get("token")
        return True

    async def get_devices(self):
        """Get list of devices from Kasa Cloud."""
        if not self._token:
            await self.login()
            
        data = await self._call("getDeviceList")
        
        if data.get("error_code") != 0:
            _LOGGER.info("Error getting devices, retrying login: %s", data)
            await self.login()
            data = await self._call("getDeviceList")
            
            if data.get("error_code") != 0:
                 raise Exception(f"Get devices failed: {data}")

        result = data.get("result", {})
        device_list = result.get("deviceList", [])
        
        return [KasaCloudDevice(d, self) for d in device_list]

    async def passthrough(self, device_id: str, command: dict, app_url: str = None, context: dict = None) -> dict:
        """Send passthrough command to a device."""
        if not self._token:
            await self.login()
            
        request_data = json.dumps(command)
        
        params = {
            "deviceId": device_id,
            "requestData": request_data
        }
        
        if context:
             # In Kasa cloud passthrough, we include context inside the requestData JSON
             command.update({"context": context})
             request_data = json.dumps(command)
             params["requestData"] = request_data

        data = await self._call("passthrough", params, url_override=app_url)
        
        if data.get("error_code") != 0:
            _LOGGER.info("Error in passthrough, retrying login: %s", data)
            await self.login()
            data = await self._call("passthrough", params, url_override=app_url)
            
        return data
