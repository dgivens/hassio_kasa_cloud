"""TP-Link Kasa Cloud API client.

Talks the legacy ("v1") Kasa cloud protocol: an unsigned JSON POST to
``wap.tplinkcloud.com``, then per-device ``passthrough`` calls in which the
device command is carried as a JSON *string* in ``requestData``.

Design rules enforced here, because the audited upstream broke all of them:

* Failures raise typed exceptions. Nothing is swallowed and no sentinel dicts
  are returned, so Home Assistant can tell "offline" from "wrong password".
* Credentials are re-submitted only on a genuine token expiry, never on a
  transport error or an offline device. Re-authenticating on every failure
  turns a flaky link into thousands of login attempts a day.
* The session token is sent as a header and never interpolated into a URL, so
  it cannot escape into a log line via an exception string.
* ``appServerUrl`` arrives in a response body and is therefore untrusted: it
  is validated against an allowlist before it is ever sent our token.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import uuid

import aiohttp
from yarl import URL

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://wap.tplinkcloud.com"

# Only hosts under this suffix may receive our session token.
ALLOWED_HOST_SUFFIX = ".tplinkcloud.com"

# Give up rather than keep submitting credentials that are being refused.
MAX_CONSECUTIVE_LOGIN_FAILURES = 3

# Reference clients identify as the Android app; a bare "Kasa" is accepted
# today but is not what any published implementation sends.
APP_TYPE = "Kasa_Android"

REQUEST_TIMEOUT = 15

ERR_OK = 0

# Token is stale but the credentials are fine: re-login once and retry.
TOKEN_ERROR_CODES = frozenset({-20651, -20652, -20661, -20675})

# Terminal auth failures. Retrying these locks the account.
CREDENTIAL_ERROR_CODES = frozenset({-20600, -20601, -20603, -23003})

# Models with a built-in energy meter. Substring matching (the upstream
# approach) misfires: "KL125" contains "125" but has no meter.
EMETER_MODEL_PREFIXES = ("HS110", "HS300", "KP115", "KP125", "EP25")

LIGHTING_SERVICE = "smartlife.iot.smartbulb.lightingservice"
DIMMER_SERVICE = "smartlife.iot.dimmer"

# getDeviceList returns every device on the account, including Tapo hardware
# and cameras. Only these types speak the legacy relay/lighting protocol this
# client implements; `SMART.*` devices use the newer KLAP/Tapo stack, and
# cameras have nothing to switch.
SUPPORTED_DEVICE_TYPES = frozenset(
    {
        "IOT.SMARTPLUGSWITCH",  # plugs, wall switches, dimmers, power strips
        "IOT.SMARTBULB",
    }
)


class KasaCloudError(Exception):
    """Base error for all Kasa cloud failures."""


class KasaCloudConnectionError(KasaCloudError):
    """The cloud could not be reached, or returned a transport-level error."""


class KasaCloudAuthError(KasaCloudError):
    """The cloud rejected our credentials. Retrying will not help."""


def _decode_alias(value: object) -> str:
    """Decode a base64 device alias, leaving plain names untouched.

    The cloud returns some aliases base64-encoded (upstream issue #2, where
    users saw entities named ``RGlzaHdhc2hlcg==`` instead of ``Dishwasher``).
    A candidate is only accepted if it round-trips exactly and decodes to
    printable UTF-8, so a plain name that happens to be valid base64 is kept.
    """
    if not isinstance(value, str) or not value:
        return ""
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return value
    if base64.b64encode(raw).decode("ascii") != value:
        return value
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return value
    if not decoded.strip() or not decoded.isprintable():
        return value
    return decoded


def _as_int(value: object) -> int | None:
    """Coerce a cloud-supplied scalar to int, or ``None`` if it is not one.

    Every numeric field here comes from a JSON response body, so a wrong type
    is a remote input problem, not a programming error. Raising out of an
    entity property (particularly ``available``) breaks state writes.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _payload_section(result: dict, service: str, method: str) -> dict:
    """Pull ``service.method`` out of a passthrough's double-encoded reply."""
    if not isinstance(result, dict):
        return {}
    outer = result.get("result")
    response_data = outer.get("responseData") if isinstance(outer, dict) else None
    if isinstance(response_data, str):
        try:
            response_data = json.loads(response_data)
        except ValueError as err:
            raise KasaCloudError("Malformed responseData from TP-Link cloud") from err
    if not isinstance(response_data, dict):
        return {}
    # `or {}` alone is not enough: a truthy non-dict survives it and then
    # explodes on .get().
    service_payload = response_data.get(service)
    if not isinstance(service_payload, dict):
        return {}
    section = service_payload.get(method)
    if not isinstance(section, dict):
        return {}

    # The envelope's error_code only says the *cloud* accepted the request.
    # The device reports its own verdict here, so a command can be rejected
    # inside an otherwise successful response.
    err_code = section.get("err_code")
    if err_code not in (None, 0):
        err_msg = section.get("err_msg")
        detail = f" {err_msg}" if isinstance(err_msg, str) else ""
        raise KasaCloudError(
            f"Device rejected {service}.{method} (err_code={err_code}{detail})"
        )
    return section


class KasaCloudDevice:
    """A device as seen through the Kasa cloud."""

    def __init__(self, device_info: dict, client: KasaCloudClient) -> None:
        self.device_info = device_info
        self.client = client
        self.device_id = device_info.get("deviceId")
        self.sys_info: dict = {}
        self._emeter_realtime: dict = {}
        self._child_emeter: dict[str, dict] = {}

    # -- identity ---------------------------------------------------------

    @property
    def alias(self) -> str:
        raw = self.sys_info.get("alias") or self.device_info.get("alias") or ""
        return _decode_alias(raw)

    @property
    def host(self) -> str | None:
        """Stable key for this device. Cloud devices have no reachable host."""
        return self.device_id

    @property
    def model(self) -> str:
        return str(self.device_info.get("deviceModel") or "")

    @property
    def device_type(self) -> str:
        return str(self.device_info.get("deviceType") or "")

    @property
    def mac(self) -> str | None:
        return self.device_info.get("deviceMac")

    @property
    def app_server_url(self) -> str | None:
        return self.device_info.get("appServerUrl")

    @property
    def hw_info(self) -> dict:
        return {
            "sw_ver": self.sys_info.get("sw_ver"),
            "hw_ver": self.sys_info.get("hw_ver"),
            "mac": self.mac,
            "model": self.model,
        }

    # -- capabilities -----------------------------------------------------

    @property
    def is_supported(self) -> bool:
        """Whether this client can actually control the device."""
        return self.device_type.upper() in SUPPORTED_DEVICE_TYPES

    @property
    def is_bulb(self) -> bool:
        return "SMARTBULB" in self.device_type.upper() or "light_state" in self.sys_info

    @property
    def is_plug(self) -> bool:
        return not self.is_bulb and "SMARTPLUGSWITCH" in self.device_type.upper()

    @property
    def is_wall_switch(self) -> bool:
        return self.is_plug and "HS2" in self.model.upper()

    @property
    def is_strip(self) -> bool:
        return self.has_children

    @property
    def is_dimmable(self) -> bool:
        if self.is_bulb:
            return True
        # Wall dimmers report brightness without a light_state block.
        return "brightness" in self.sys_info

    @property
    def is_variable_color_temp(self) -> bool:
        return "color_temp" in self._light_state

    @property
    def is_color(self) -> bool:
        return "hue" in self._light_state or "hsv" in self.sys_info

    @property
    def has_emeter(self) -> bool:
        # `feature` ("TIM:ENE") is reported by get_sysinfo, not by the cloud's
        # device list, so sys_info is the authoritative source once polled.
        feature = self.sys_info.get("feature") or self.device_info.get("feature") or ""
        if "ENE" in str(feature):
            return True
        return self.model.upper().startswith(EMETER_MODEL_PREFIXES)

    @property
    def has_children(self) -> bool:
        return bool(self.sys_info.get("children"))

    @property
    def children(self) -> list[KasaCloudChildDevice]:
        """Child outlets, resolved live against the most recent poll."""
        return [
            KasaCloudChildDevice(self, child["id"])
            for child in self.sys_info.get("children") or []
            if child.get("id")
        ]

    # -- state ------------------------------------------------------------

    @property
    def _light_state(self) -> dict:
        state = self.sys_info.get("light_state")
        return state if isinstance(state, dict) else {}

    @property
    def is_on(self) -> bool | None:
        """``None`` when unknown, so HA shows 'unknown' rather than 'off'."""
        if not self.sys_info:
            return None
        if "relay_state" in self.sys_info:
            return self.sys_info["relay_state"] == 1
        if "on_off" in self._light_state:
            return self._light_state["on_off"] == 1
        return None

    @property
    def brightness(self) -> int | None:
        """Brightness on Home Assistant's 0-255 scale."""
        raw = _as_int(self._light_state.get("brightness", self.sys_info.get("brightness")))
        if raw is None:
            return None
        return round(raw * 255 / 100)

    @property
    def color_temp(self) -> int | None:
        """Colour temperature in Kelvin, or ``None`` if unsupported."""
        raw = _as_int(self._light_state.get("color_temp"))
        return raw or None

    @property
    def hsv(self) -> tuple[int, int, int] | None:
        """Hue, saturation and 0-100 value, or ``None`` if not a colour bulb."""
        state = self._light_state
        if "hue" not in state:
            return None
        return (
            _as_int(state.get("hue")) or 0,
            _as_int(state.get("saturation")) or 0,
            _as_int(state.get("brightness")) or 0,
        )

    @property
    def emeter_realtime(self) -> dict:
        return self._emeter_realtime

    @property
    def rssi(self) -> int | None:
        return self.sys_info.get("rssi")

    @property
    def on_since(self) -> int | None:
        return self.sys_info.get("on_time")

    @property
    def overheated(self) -> bool | None:
        raw = self.sys_info.get("overheated")
        if raw is None:
            return None
        return bool(raw)

    @property
    def led_status(self) -> bool | None:
        """``True`` when the status LED is lit. ``led_off`` is inverted."""
        if "led_off" not in self.sys_info:
            return None
        return self.sys_info["led_off"] == 0

    @property
    def status(self) -> int | None:
        return _as_int(self.device_info.get("status"))

    @property
    def is_connected(self) -> bool | None:
        """Cloud-reported reachability, refreshed by the coordinator."""
        if self.status is None:
            return None
        return self.status == 1

    # -- operations -------------------------------------------------------

    async def update(self, include_emeter: bool = True) -> None:
        """Refresh cached state. Raises so the coordinator can go unavailable."""
        try:
            result = await self.client.passthrough(
                self.device_id, {"system": {"get_sysinfo": {}}}, self.app_server_url
            )
            sys_info = _payload_section(result, "system", "get_sysinfo")
        except KasaCloudError:
            # Never leave stale readings behind to be reported as live.
            self.sys_info = {}
            self._emeter_realtime = {}
            self._child_emeter = {}
            raise

        self.sys_info = sys_info

        # Deliberately outside the block above: energy is secondary data, and a
        # flaky read of it must not discard the state we just fetched
        # successfully, nor block setup.
        if include_emeter and self.has_emeter:
            await self._update_emeter()

    async def _update_emeter(self) -> None:
        """Refresh energy readings. Never raises."""
        command = {"emeter": {"get_realtime": {}}}

        if self.has_children:
            readings = dict(self._child_emeter)
            for child in self.sys_info.get("children") or []:
                child_id = child.get("id")
                if not child_id:
                    continue
                readings[child_id] = await self._read_emeter(
                    command, context={"child_ids": [child_id]}
                )
            self._child_emeter = readings
            return

        self._emeter_realtime = await self._read_emeter(command)

    async def _read_emeter(self, command: dict, context: dict | None = None) -> dict:
        try:
            result = await self.client.passthrough(
                self.device_id, command, self.app_server_url, context=context
            )
            return _payload_section(result, "emeter", "get_realtime")
        except KasaCloudError as err:
            _LOGGER.debug(
                "Energy read failed for %s%s: %s",
                self.device_id,
                f" child {context['child_ids'][0]}" if context else "",
                err,
            )
            return {}

    async def _send(
        self, service: str, method: str, args: dict, context: dict | None = None
    ) -> dict:
        """Send a command and confirm the device accepted it."""
        result = await self.client.passthrough(
            self.device_id, {service: {method: args}}, self.app_server_url, context=context
        )
        # Raises if the device reported a non-zero err_code.
        return _payload_section(result, service, method)

    async def turn_on(self) -> None:
        if self.is_bulb:
            await self._send(LIGHTING_SERVICE, "transition_light_state", {"on_off": 1})
            return
        await self._send("system", "set_relay_state", {"state": 1})

    async def turn_off(self) -> None:
        if self.is_bulb:
            await self._send(LIGHTING_SERVICE, "transition_light_state", {"on_off": 0})
            return
        await self._send("system", "set_relay_state", {"state": 0})

    async def set_brightness(self, brightness: int) -> None:
        """Set brightness from Home Assistant's 0-255 scale."""
        kasa_pct = max(1, round(int(brightness) * 100 / 255))
        if self.is_bulb:
            await self._send(
                LIGHTING_SERVICE,
                "transition_light_state",
                {"brightness": kasa_pct, "on_off": 1},
            )
            return
        # A wall dimmer has no lightingservice; brightness and relay are separate.
        await self._send("system", "set_relay_state", {"state": 1})
        await self._send(DIMMER_SERVICE, "set_brightness", {"brightness": kasa_pct})

    async def set_color_temp(self, kelvin: int) -> None:
        await self._send(
            LIGHTING_SERVICE,
            "transition_light_state",
            {"color_temp": int(kelvin), "on_off": 1},
        )

    async def set_hsv(self, hue: int, saturation: int, value: int) -> None:
        await self._send(
            LIGHTING_SERVICE,
            "transition_light_state",
            {
                "hue": int(hue),
                "saturation": int(saturation),
                "brightness": int(value),
                "color_temp": 0,
                "on_off": 1,
            },
        )

    async def set_led(self, on: bool) -> None:
        await self._send("system", "set_led_off", {"off": 0 if on else 1})

    async def reboot(self) -> None:
        await self._send("system", "reboot", {"delay": 1})


class KasaCloudChildDevice:
    """A single outlet on a power strip such as the HS300.

    State is resolved against the parent's current ``sys_info`` on every
    access. Upstream cached the child's dict at setup time, which the parent
    then replaced on each poll, freezing every outlet's state permanently.
    """

    def __init__(self, parent: KasaCloudDevice, child_id: str) -> None:
        self.parent = parent
        self.device_id = child_id
        self._id = child_id

    @property
    def data(self) -> dict:
        for child in self.parent.sys_info.get("children") or []:
            if child.get("id") == self._id:
                return child
        return {}

    @property
    def alias(self) -> str:
        return _decode_alias(self.data.get("alias") or "")

    @property
    def is_on(self) -> bool | None:
        state = self.data.get("state")
        return None if state is None else state == 1

    @property
    def device_info(self) -> dict:
        info = dict(self.parent.device_info)
        info["deviceId"] = self._id
        info["alias"] = self.alias
        return info

    @property
    def model(self) -> str:
        return self.parent.model

    @property
    def hw_info(self) -> dict:
        return self.parent.hw_info

    @property
    def has_emeter(self) -> bool:
        return self.parent.has_emeter

    @property
    def emeter_realtime(self) -> dict:
        return self.parent._child_emeter.get(self._id, {})

    @property
    def on_since(self) -> int | None:
        return self.data.get("on_time")

    @property
    def is_connected(self) -> bool | None:
        return self.parent.is_connected

    @property
    def is_bulb(self) -> bool:
        return False

    @property
    def is_dimmable(self) -> bool:
        return False

    async def _set_relay(self, state: int) -> None:
        # Routed through the parent's _send so the device's own err_code is
        # checked; a rejected command must not look like a success.
        await self.parent._send(
            "system",
            "set_relay_state",
            {"state": state},
            context={"child_ids": [self._id]},
        )

    async def turn_on(self) -> None:
        await self._set_relay(1)

    async def turn_off(self) -> None:
        await self._set_relay(0)


class KasaCloudClient:
    """Authenticated client for the Kasa cloud."""

    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
        terminal_uuid: str | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self._session = session
        # Stable across restarts when persisted by the caller, so the account
        # does not accumulate a new "terminal" on every Home Assistant boot.
        self._terminal_uuid = terminal_uuid or str(uuid.uuid4())
        self._token: str | None = None
        # Serialises logins so N devices expiring together cause one login,
        # not N simultaneous ones with the same credentials.
        self._login_lock = asyncio.Lock()
        # Bumped on every successful login, so a caller holding a stale token
        # can tell whether someone else already refreshed it.
        self._token_generation = 0
        self._login_failures = 0
        self._rejected_hosts: set[str | None] = set()

    @property
    def terminal_uuid(self) -> str:
        return self._terminal_uuid

    def _endpoint(self, url_override: str | None) -> URL:
        """Resolve the request URL, refusing anything not clearly TP-Link's.

        ``appServerUrl`` comes from a response body, so it is attacker- or
        misconfiguration-controlled. The URL is rebuilt from validated parts
        rather than passed through: embedded userinfo would otherwise reach
        aiohttp, which turns it into BasicAuth.
        """
        if not url_override:
            return URL(BASE_URL)

        candidate: URL | None
        try:
            candidate = URL(url_override)
        except (ValueError, TypeError):
            candidate = None

        host = candidate.host if candidate is not None else None
        if (
            candidate is None
            or candidate.scheme != "https"
            or not host
            or not (host == "wap.tplinkcloud.com" or host.endswith(ALLOWED_HOST_SUFFIX))
            # Credentials in the URL, or a non-standard port, mean this is not
            # the endpoint we think it is.
            or candidate.user
            or candidate.password
            or candidate.port not in (None, 443)
        ):
            if host not in self._rejected_hosts:
                self._rejected_hosts.add(host)
                _LOGGER.warning(
                    "Ignoring untrusted appServerUrl host %r; using %s", host, BASE_URL
                )
            return URL(BASE_URL)

        # Rebuild from validated components; drops userinfo, query and fragment.
        return URL.build(scheme="https", host=host, path=candidate.path or "/")

    async def _call(
        self, method: str, params: dict | None = None, url_override: str | None = None
    ) -> dict:
        url = self._endpoint(url_override).with_query({"termID": self._terminal_uuid})

        # The token travels in the JSON body, never in the URL and never in a
        # header. The v1 API reads it from `params.token`; putting it in the
        # query string is also accepted but leaks it into every exception
        # message, proxy log and `ClientResponseError.__str__`.
        request_params = dict(params or {})
        if self._token and method != "login":
            request_params["token"] = self._token
        payload = {"method": method, "params": request_params}

        try:
            async with self._session.post(
                url,
                json=payload,
                # A 307/308 replays the request body — which for `login` is the
                # cleartext password — at a location we never validated, and to
                # a scheme aiohttp permits downgrading to http.
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as response:
                if 300 <= response.status < 400:
                    raise KasaCloudConnectionError(
                        f"TP-Link cloud attempted a redirect for {method}; refusing"
                    )
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientResponseError as err:
            # `from None`: the original message embeds the request URL.
            raise KasaCloudConnectionError(
                f"TP-Link cloud returned HTTP {err.status} for {method}"
            ) from None
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            # ValueError also covers json.JSONDecodeError and aiohttp.InvalidURL.
            raise KasaCloudConnectionError(
                f"Cannot reach TP-Link cloud ({type(err).__name__}) for {method}"
            ) from None

    def _raise_for_code(
        self, code: object, what: str, *, credentials_checked: bool = False
    ) -> None:
        """Convert an error code to an exception.

        Only a ``login`` reply can tell us the credentials are wrong. Treating
        a device-level code as a credential failure would stop polling the
        whole account and prompt for a password that was never wrong.
        """
        if credentials_checked and code in CREDENTIAL_ERROR_CODES:
            raise KasaCloudAuthError(
                f"TP-Link cloud rejected the account credentials "
                f"during {what} (error_code={code})"
            )
        if code != ERR_OK:
            raise KasaCloudError(f"{what} failed (error_code={code})")

    async def login(self) -> None:
        """Authenticate, replacing any existing token."""
        async with self._login_lock:
            await self._login_locked()

    async def _login_locked(self) -> None:
        if self._login_failures >= MAX_CONSECUTIVE_LOGIN_FAILURES:
            raise KasaCloudAuthError(
                "Repeated TP-Link cloud login failures; re-authentication required"
            )

        data = await self._call(
            "login",
            {
                "appType": APP_TYPE,
                "cloudUserName": self._username,
                "cloudPassword": self._password,
                "terminalUUID": self._terminal_uuid,
            },
        )

        code = data.get("error_code")
        if code != ERR_OK:
            self._login_failures += 1
            if code in TOKEN_ERROR_CODES:
                raise KasaCloudAuthError(f"login rejected (error_code={code})")
            self._raise_for_code(code, "login", credentials_checked=True)

        result = data.get("result")
        token = result.get("token") if isinstance(result, dict) else None
        if not token or not isinstance(token, str):
            self._login_failures += 1
            raise KasaCloudAuthError("TP-Link cloud login returned no token")

        self._token = token
        self._token_generation += 1
        self._login_failures = 0

    async def _ensure_token(self, seen_generation: int | None = None) -> None:
        """Obtain a token, unless another caller already refreshed it."""
        async with self._login_lock:
            if self._token is not None and (
                seen_generation is None or seen_generation != self._token_generation
            ):
                return
            await self._login_locked()

    async def _call_with_reauth(
        self,
        method: str,
        params: dict | None = None,
        url_override: str | None = None,
    ) -> dict:
        """Call ``method``, re-authenticating at most once on token expiry."""
        await self._ensure_token()
        generation = self._token_generation

        data = await self._call(method, params, url_override)
        if data.get("error_code") in TOKEN_ERROR_CODES:
            await self._ensure_token(seen_generation=generation)
            data = await self._call(method, params, url_override)

            if data.get("error_code") in TOKEN_ERROR_CODES:
                # A freshly issued token was refused, so the token is not being
                # accepted at all. Retrying can only hammer the login endpoint,
                # so surface this as an auth failure: Home Assistant then stops
                # polling and asks the user to reauthenticate.
                self._token = None
                raise KasaCloudAuthError(
                    f"TP-Link cloud refused a freshly issued token for {method} "
                    f"(error_code={data.get('error_code')})"
                )

        self._raise_for_code(data.get("error_code"), method)
        return data

    async def fetch_device_records(self) -> list[dict]:
        """Return the raw ``getDeviceList`` entries."""
        data = await self._call_with_reauth("getDeviceList")
        return (data.get("result") or {}).get("deviceList") or []

    async def get_devices(self) -> list[KasaCloudDevice]:
        """Return the devices on the account that this client can control."""
        devices = [
            KasaCloudDevice(record, self) for record in await self.fetch_device_records()
        ]

        supported = [device for device in devices if device.is_supported]
        for device in devices:
            if not device.is_supported:
                _LOGGER.debug(
                    "Ignoring unsupported device %s (type %r): this integration "
                    "only handles legacy Kasa plugs, switches and bulbs",
                    device.device_id,
                    device.device_type,
                )
        return supported

    async def passthrough(
        self,
        device_id: str,
        command: dict,
        app_url: str | None = None,
        context: dict | None = None,
    ) -> dict:
        """Send a device command. ``requestData`` must be a JSON *string*."""
        request = dict(command)
        if context:
            request["context"] = context
        params = {"deviceId": device_id, "requestData": json.dumps(request)}
        return await self._call_with_reauth("passthrough", params, url_override=app_url)
