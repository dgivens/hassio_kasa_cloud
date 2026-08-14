"""Tests for the TP-Link Kasa Cloud client.

These cover the bugs found in the upstream audit. `cloud_api` deliberately
depends only on aiohttp + stdlib, so it is testable without Home Assistant.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import aiohttp
import pytest

# Load cloud_api.py directly: importing it via the package would pull in
# custom_components/kasa_cloud/__init__.py, which requires Home Assistant.
_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "kasa_cloud"
    / "cloud_api.py"
)
_spec = importlib.util.spec_from_file_location("kasa_cloud_api", _MODULE_PATH)
cloud_api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cloud_api)

BASE_URL = cloud_api.BASE_URL
KasaCloudAuthError = cloud_api.KasaCloudAuthError
KasaCloudClient = cloud_api.KasaCloudClient
KasaCloudConnectionError = cloud_api.KasaCloudConnectionError
KasaCloudDevice = cloud_api.KasaCloudDevice
KasaCloudError = cloud_api.KasaCloudError


# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload, status: int = 200, url: str = ""):
        self._payload = payload
        self.status = status
        self._url = url

    async def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=aiohttp.RequestInfo(
                    url=self._url, method="POST", headers=None, real_url=self._url
                ),
                history=(),
                status=self.status,
                message="Server Error",
            )

    async def __aenter__(self):
        # Yield to the event loop so gathered tasks genuinely interleave;
        # without this every "concurrent" call runs start-to-finish in turn.
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Records every request and replays canned responses."""

    def __init__(self, handler):
        self._handler = handler
        self.requests: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None, allow_redirects=True):
        url = str(url)
        self.requests.append(
            {
                "url": url,
                "json": json,
                "headers": headers or {},
                "allow_redirects": allow_redirects,
            }
        )
        return self._handler(url, json)

    @property
    def methods_called(self) -> list[str]:
        return [r["json"]["method"] for r in self.requests if r["json"]]

    def request_data(self, index: int) -> dict:
        """Decode the double-encoded requestData of the Nth request."""
        return json.loads(self.requests[index]["json"]["params"]["requestData"])


LOGIN_OK = {"error_code": 0, "result": {"token": "TOKEN-ABC123"}}


def sysinfo_response(sysinfo: dict) -> dict:
    return {
        "error_code": 0,
        "result": {"responseData": json.dumps({"system": {"get_sysinfo": sysinfo}})},
    }


def emeter_response(realtime: dict) -> dict:
    return {
        "error_code": 0,
        "result": {"responseData": json.dumps({"emeter": {"get_realtime": realtime}})},
    }


def serve_device_call(payload: dict, sysinfo: dict | None = None) -> dict:
    """Answer a passthrough by looking at what it actually asked for.

    An HS300 poll is a get_sysinfo followed by one emeter call per outlet, so
    fakes must dispatch on content rather than assume a fixed call sequence.
    """
    command = json.loads(payload["params"]["requestData"])
    if "emeter" in command:
        return emeter_response({})
    return sysinfo_response(sysinfo if sysinfo is not None else HS300_SYSINFO)


HS300_SYSINFO = {
    "alias": "UGllciBTdHJpcA==",  # "Pier Strip"
    "model": "HS300(US)",
    "children": [
        {"id": "CHILD0", "alias": "VG9hc3Rlcg==", "state": 0},  # "Toaster"
        {"id": "CHILD1", "alias": "Mount", "state": 1},
    ],
}


def hs300_device(client: KasaCloudClient) -> KasaCloudDevice:
    return KasaCloudDevice(
        {
            "deviceId": "DEV1",
            "alias": "UGllciBTdHJpcA==",
            "deviceType": "IOT.SMARTPLUGSWITCH",
            "deviceModel": "HS300(US)",
            "appServerUrl": "https://use1-wap.tplinkcloud.com",
            "feature": "TIM:ENE",
            "status": 1,
        },
        client,
    )


def make_client(handler) -> tuple[KasaCloudClient, FakeSession]:
    session = FakeSession(handler)
    return KasaCloudClient("user@example.com", "pw", session=session), session


# --------------------------------------------------------------------------
# Availability: failures must propagate, not be swallowed
# --------------------------------------------------------------------------

async def test_update_raises_when_cloud_reports_device_offline():
    """A failed poll must raise so the coordinator can mark entities unavailable."""

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        return FakeResponse({"error_code": -20571, "msg": "Device is offline"}, url=url)

    client, _ = make_client(handler)
    device = hs300_device(client)

    with pytest.raises(KasaCloudError):
        await device.update()


async def test_update_clears_cached_state_on_failure():
    """Stale sys_info must not survive a failed poll and be reported as live."""
    state = {"fail": False}

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        if state["fail"]:
            return FakeResponse({"error_code": -20571}, url=url)
        return FakeResponse(serve_device_call(payload), url=url)

    client, _ = make_client(handler)
    device = hs300_device(client)

    await device.update()
    assert device.sys_info != {}

    state["fail"] = True
    with pytest.raises(KasaCloudError):
        await device.update()
    assert device.sys_info == {}


async def test_is_on_is_none_when_state_unknown():
    """Unknown must not be rendered as a confident 'off'."""
    client, _ = make_client(lambda url, payload: FakeResponse(LOGIN_OK, url=url))
    device = hs300_device(client)

    assert device.is_on is None


# --------------------------------------------------------------------------
# Commands must not fail silently
# --------------------------------------------------------------------------

async def test_turn_on_raises_when_command_rejected():
    """A rejected relay command must not report success."""

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        return FakeResponse({"error_code": -20571, "msg": "Device is offline"}, url=url)

    client, _ = make_client(handler)
    device = hs300_device(client)

    with pytest.raises(KasaCloudError):
        await device.turn_on()


# --------------------------------------------------------------------------
# No login storm: re-auth only on genuine auth failures
# --------------------------------------------------------------------------

async def test_offline_device_does_not_trigger_relogin():
    """'Device offline' is not an auth problem and must not re-submit credentials."""

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        return FakeResponse({"error_code": -20571}, url=url)

    client, session = make_client(handler)
    device = hs300_device(client)

    with pytest.raises(KasaCloudError):
        await device.update()

    assert session.methods_called.count("login") == 1


async def test_expired_token_triggers_exactly_one_relogin():
    state = {"expired": True}

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        if state["expired"]:
            state["expired"] = False
            return FakeResponse({"error_code": -20651}, url=url)
        return FakeResponse(serve_device_call(payload), url=url)

    client, session = make_client(handler)
    device = hs300_device(client)

    await device.update()

    assert session.methods_called.count("login") == 2


async def test_bad_credentials_raise_auth_error_and_do_not_retry():
    """Wrong password must surface as an auth error, not an infinite retry."""

    def handler(url, payload):
        return FakeResponse({"error_code": -20601, "msg": "Incorrect email or password"}, url=url)

    client, session = make_client(handler)

    with pytest.raises(KasaCloudAuthError):
        await client.login()

    assert session.methods_called.count("login") == 1


async def test_transport_failure_raises_connection_error():
    def handler(url, payload):
        return FakeResponse({}, status=503, url=url)

    client, _ = make_client(handler)

    with pytest.raises(KasaCloudConnectionError):
        await client.login()


# --------------------------------------------------------------------------
# Token hygiene
# --------------------------------------------------------------------------

async def test_token_absent_from_connection_error_message():
    """The session token must never reach a log line via an exception string."""
    state = {"logged_in": False}

    def handler(url, payload):
        if payload["method"] == "login":
            state["logged_in"] = True
            return FakeResponse(LOGIN_OK, url=url)
        return FakeResponse({}, status=500, url=url)

    client, _ = make_client(handler)
    device = hs300_device(client)

    with pytest.raises(KasaCloudError) as excinfo:
        await device.update()

    assert state["logged_in"]
    assert "TOKEN-ABC123" not in str(excinfo.value)
    assert "TOKEN-ABC123" not in repr(excinfo.value)


async def test_token_sent_in_request_body_not_in_url():
    """The v1 API reads the token from `params.token`.

    It also accepts `?token=`, but that leaks it into every proxy log and into
    `ClientResponseError.__str__`. It does not accept an Authorization header,
    so sending one there authenticates nothing.
    """

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        return FakeResponse(serve_device_call(payload), url=url)

    client, session = make_client(handler)
    device = hs300_device(client)
    await device.update()

    passthrough = session.requests[-1]
    assert passthrough["json"]["params"]["token"] == "TOKEN-ABC123"
    assert "TOKEN-ABC123" not in passthrough["url"]
    assert "Authorization" not in passthrough["headers"]


async def test_login_request_does_not_carry_a_token():
    """Re-login must not present the stale token it is replacing."""
    state = {"expired": True}

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        if state["expired"]:
            state["expired"] = False
            return FakeResponse({"error_code": -20651}, url=url)
        return FakeResponse(serve_device_call(payload), url=url)

    client, session = make_client(handler)
    await hs300_device(client).update()

    logins = [r for r in session.requests if r["json"]["method"] == "login"]
    assert len(logins) == 2
    for login in logins:
        assert "token" not in login["json"]["params"]


async def test_redirects_are_refused():
    """A 307 replays the body — for `login`, the cleartext password."""

    def handler(url, payload):
        return FakeResponse({}, status=307, url=url)

    client, session = make_client(handler)

    with pytest.raises(KasaCloudConnectionError):
        await client.login()

    assert session.requests[0]["allow_redirects"] is False


# --------------------------------------------------------------------------
# appServerUrl is untrusted input
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "hostile_url",
    [
        "http://use1-wap.tplinkcloud.com",          # downgraded to cleartext
        "https://evil.example.com",                 # foreign host
        "https://tplinkcloud.com.evil.net",         # suffix confusion
        "https://wap.tplinkcloud.com@evil.com/x",   # userinfo masquerade
        "https://someone@use1-wap.tplinkcloud.com", # in-zone, but userinfo set
        "https://use1-wap.tplinkcloud.com:22",      # port smuggling
        "https://wap.tplinkcloud.com.",             # trailing dot
        "https://[::1]/",                           # loopback
    ],
)
async def test_untrusted_app_server_url_is_rejected(hostile_url):
    """A URL taken from a response body must not receive our token."""

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        return FakeResponse(sysinfo_response(HS300_SYSINFO), url=url)

    client, session = make_client(handler)
    info = hs300_device(client).device_info
    info["appServerUrl"] = hostile_url
    device = KasaCloudDevice(info, client)

    await device.update()

    for request in session.requests:
        assert hostile_url not in request["url"]
        assert request["url"].startswith(BASE_URL)


async def test_regional_endpoint_is_rebuilt_not_passed_through():
    """Query and fragment from a response-supplied URL must not survive."""

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        return FakeResponse(serve_device_call(payload), url=url)

    client, session = make_client(handler)
    info = hs300_device(client).device_info
    info["appServerUrl"] = "https://use1-wap.tplinkcloud.com/api?spoof=1#frag"
    device = KasaCloudDevice(info, client)

    await device.update()

    url = session.requests[-1]["url"]
    assert url.startswith("https://use1-wap.tplinkcloud.com/api?termID=")
    assert "spoof" not in url
    assert "frag" not in url


async def test_device_error_code_is_not_mistaken_for_bad_credentials():
    """Only a login reply can tell us the password is wrong.

    Misclassifying a device-level code would stop polling the whole account and
    prompt the user to re-enter a password that was never wrong.
    """

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        # -23003 is in CREDENTIAL_ERROR_CODES, but arriving here it describes
        # the device, not the account.
        return FakeResponse({"error_code": -23003}, url=url)

    client, _ = make_client(handler)
    device = hs300_device(client)

    with pytest.raises(KasaCloudError) as excinfo:
        await device.update()
    assert not isinstance(excinfo.value, KasaCloudAuthError)


async def test_legitimate_regional_endpoint_is_used():
    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        return FakeResponse(sysinfo_response(HS300_SYSINFO), url=url)

    client, session = make_client(handler)
    device = hs300_device(client)
    await device.update()

    assert session.requests[-1]["url"].startswith("https://use1-wap.tplinkcloud.com")


# --------------------------------------------------------------------------
# Base64 aliases (upstream issue #2)
# --------------------------------------------------------------------------

def test_base64_alias_is_decoded():
    client, _ = make_client(lambda url, payload: None)
    device = hs300_device(client)

    assert device.alias == "Pier Strip"


def test_plain_alias_is_left_alone():
    client, _ = make_client(lambda url, payload: None)
    info = hs300_device(client).device_info
    info["alias"] = "Observatory Pier"
    device = KasaCloudDevice(info, client)

    assert device.alias == "Observatory Pier"


# --------------------------------------------------------------------------
# HS300: child outlets must not freeze, and must report energy
# --------------------------------------------------------------------------

async def test_child_state_follows_latest_poll():
    """Regression: children were bound to the setup-time sys_info dict forever."""
    flipped = json.loads(json.dumps(HS300_SYSINFO))
    flipped["children"][0]["state"] = 1
    state = {"sysinfo": HS300_SYSINFO}

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        return FakeResponse(serve_device_call(payload, state["sysinfo"]), url=url)

    client, _ = make_client(handler)
    device = hs300_device(client)

    await device.update()
    child = device.children[0]
    assert child.is_on is False

    state["sysinfo"] = flipped
    await device.update()
    assert child.is_on is True, "child state is stale after a fresh poll"


async def test_child_alias_is_decoded():
    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        return FakeResponse(sysinfo_response(HS300_SYSINFO), url=url)

    client, _ = make_client(handler)
    device = hs300_device(client)
    await device.update()

    assert [c.alias for c in device.children] == ["Toaster", "Mount"]


async def test_child_command_includes_child_context():
    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        return FakeResponse(sysinfo_response(HS300_SYSINFO), url=url)

    client, session = make_client(handler)
    device = hs300_device(client)
    await device.update()

    await device.children[0].turn_on()

    sent = session.request_data(-1)
    assert sent["context"]["child_ids"] == ["CHILD0"]
    assert sent["system"]["set_relay_state"]["state"] == 1


def test_hs300_reports_emeter_by_model():
    """'HS300' matches none of the substrings the audit flagged."""
    client, _ = make_client(lambda url, payload: None)

    assert hs300_device(client).has_emeter is True


def test_emeter_detected_from_sysinfo_feature_flag():
    """A metered model we do not list must still be detected via `feature`.

    The flag lives in get_sysinfo, not in the cloud's device list, so this uses
    a model matching no prefix to actually exercise that branch.
    """
    client, _ = make_client(lambda url, payload: None)
    device = KasaCloudDevice(
        {"deviceId": "NEW1", "deviceModel": "KP999(US)", "deviceType": "IOT.SMARTPLUGSWITCH"},
        client,
    )
    assert device.has_emeter is False

    device.sys_info = {"relay_state": 1, "feature": "TIM:ENE"}
    assert device.has_emeter is True


def test_bulb_model_is_not_treated_as_emeter_device():
    """KL125 contains '125' but has no energy meter."""
    client, _ = make_client(lambda url, payload: None)
    device = KasaCloudDevice(
        {"deviceId": "B1", "deviceModel": "KL125(US)", "deviceType": "IOT.SMARTBULB", "feature": "TIM"},
        client,
    )

    assert device.has_emeter is False


async def test_child_emeter_is_fetched_per_outlet():
    """HS300 energy lives per child and needs a child_ids context."""

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        data = json.loads(payload["params"]["requestData"])
        if "emeter" in data:
            child = data["context"]["child_ids"][0]
            watts = {"CHILD0": 12.5, "CHILD1": 40.0}[child]
            return FakeResponse(emeter_response({"power_mw": int(watts * 1000)}), url=url)
        return FakeResponse(sysinfo_response(HS300_SYSINFO), url=url)

    client, _ = make_client(handler)
    device = hs300_device(client)

    await device.update()

    assert device.children[0].emeter_realtime["power_mw"] == 12500
    assert device.children[1].emeter_realtime["power_mw"] == 40000


# --------------------------------------------------------------------------
# The device's own err_code, inside an otherwise successful envelope
# --------------------------------------------------------------------------

async def test_device_level_rejection_is_not_reported_as_success():
    """`error_code: 0` only means the *cloud* accepted the request."""

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        return FakeResponse(
            {
                "error_code": 0,
                "result": {
                    "responseData": json.dumps(
                        {"system": {"set_relay_state": {"err_code": -3, "err_msg": "invalid argument"}}}
                    )
                },
            },
            url=url,
        )

    client, _ = make_client(handler)
    device = hs300_device(client)

    with pytest.raises(KasaCloudError, match="err_code=-3"):
        await device.turn_on()


async def test_child_outlet_rejection_is_not_reported_as_success():
    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        command = json.loads(payload["params"]["requestData"])
        if "system" in command and "set_relay_state" in command["system"]:
            return FakeResponse(
                {
                    "error_code": 0,
                    "result": {
                        "responseData": json.dumps(
                            {"system": {"set_relay_state": {"err_code": -3}}}
                        )
                    },
                },
                url=url,
            )
        return FakeResponse(serve_device_call(payload), url=url)

    client, _ = make_client(handler)
    device = hs300_device(client)
    await device.update()

    with pytest.raises(KasaCloudError):
        await device.children[0].turn_on()


# --------------------------------------------------------------------------
# Energy is secondary: a failed read must not discard good state
# --------------------------------------------------------------------------

async def test_failed_energy_read_keeps_device_state():
    """One flaky emeter call must not blank all six outlets."""

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        command = json.loads(payload["params"]["requestData"])
        if "emeter" in command:
            return FakeResponse({"error_code": -20571}, url=url)
        return FakeResponse(sysinfo_response(HS300_SYSINFO), url=url)

    client, _ = make_client(handler)
    device = hs300_device(client)

    await device.update()  # must not raise

    assert device.sys_info != {}
    assert device.children[0].is_on is False
    assert device.children[0].emeter_realtime == {}


async def test_energy_is_not_polled_when_not_requested():
    """State polls far more often than energy; 6 outlets = 6 extra calls."""

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        return FakeResponse(serve_device_call(payload), url=url)

    client, session = make_client(handler)
    device = hs300_device(client)

    await device.update(include_emeter=False)

    commands = [
        json.loads(r["json"]["params"]["requestData"])
        for r in session.requests
        if r["json"]["method"] == "passthrough"
    ]
    assert len(commands) == 1
    assert "emeter" not in commands[0]


async def test_energy_poll_costs_one_call_per_outlet():
    """Pins the cloud budget so it cannot regress silently."""

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        return FakeResponse(serve_device_call(payload), url=url)

    client, session = make_client(handler)
    await hs300_device(client).update(include_emeter=True)

    passthroughs = [r for r in session.requests if r["json"]["method"] == "passthrough"]
    assert len(passthroughs) == 3  # 1 sysinfo + 1 per child outlet


# --------------------------------------------------------------------------
# The login storm must be able to terminate
# --------------------------------------------------------------------------

async def test_persistently_refused_token_becomes_an_auth_error():
    """If a *fresh* token is refused, retrying can only hammer the endpoint.

    Escalating to an auth error is what lets Home Assistant stop polling and
    ask for reauthentication instead of looping forever.
    """

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        return FakeResponse({"error_code": -20651}, url=url)

    client, session = make_client(handler)
    device = hs300_device(client)

    with pytest.raises(KasaCloudAuthError):
        await device.update()

    # Two logins at most: the initial one and a single refresh attempt.
    assert session.methods_called.count("login") == 2


async def test_repeated_login_failures_stop_rather_than_retry_forever():
    def handler(url, payload):
        return FakeResponse({"error_code": -20004, "msg": "rate limited"}, url=url)

    client, session = make_client(handler)

    for _ in range(6):
        with pytest.raises(KasaCloudError):
            await client.login()

    # Capped, not one attempt per call.
    assert session.methods_called.count("login") == 3


def plain_plugs(client: KasaCloudClient, count: int = 3) -> list[KasaCloudDevice]:
    return [
        KasaCloudDevice(
            {
                "deviceId": f"PLUG{index}",
                "deviceType": "IOT.SMARTPLUGSWITCH",
                "deviceModel": "HS103(US)",
            },
            client,
        )
        for index in range(count)
    ]


async def test_cold_start_with_many_devices_logs_in_once():
    """N devices starting together must not fire N simultaneous logins."""

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        return FakeResponse(sysinfo_response({"relay_state": 1}), url=url)

    client, session = make_client(handler)
    devices = plain_plugs(client)

    await asyncio.gather(*(device.update() for device in devices))

    assert session.methods_called.count("login") == 1


async def test_shared_token_expiry_causes_a_single_relogin():
    """A token expiry is global, so one refresh must serve every device.

    Without a lock and a token generation, each device would notice
    independently and start its own login with the same credentials.
    """
    server = {"valid": "TOKEN-ABC123", "issued": 0}

    def handler(url, payload):
        if payload["method"] == "login":
            server["issued"] += 1
            server["valid"] = f"FRESH-{server['issued']}"
            return FakeResponse(
                {"error_code": 0, "result": {"token": server["valid"]}}, url=url
            )
        if payload["params"].get("token") != server["valid"]:
            return FakeResponse({"error_code": -20651}, url=url)
        return FakeResponse(sysinfo_response({"relay_state": 1}), url=url)

    client, session = make_client(handler)
    # Start from a token the server no longer accepts.
    client._token = "STALE"
    client._token_generation = 1
    devices = plain_plugs(client)

    await asyncio.gather(*(device.update() for device in devices))

    assert session.methods_called.count("login") == 1
    assert all(device.is_on is True for device in devices)


# --------------------------------------------------------------------------
# Robustness against partial cloud payloads
# --------------------------------------------------------------------------

def test_missing_device_type_and_model_do_not_raise():
    """A partial record for one device must not take down a whole platform."""
    client, _ = make_client(lambda url, payload: None)
    device = KasaCloudDevice({"deviceId": "X1"}, client)

    assert device.is_plug is False
    assert device.is_bulb is False
    assert device.is_dimmable is False
    assert device.has_emeter is False
    assert device.model == ""


# --------------------------------------------------------------------------
# Session lifecycle
# --------------------------------------------------------------------------

def bulb_device(client: KasaCloudClient) -> KasaCloudDevice:
    device = KasaCloudDevice(
        {
            "deviceId": "BULB1",
            "alias": "Dome Light",
            "deviceType": "IOT.SMARTBULB",
            "deviceModel": "KL130(US)",
            "appServerUrl": "https://use1-wap.tplinkcloud.com",
        },
        client,
    )
    device.sys_info = {
        "light_state": {"on_off": 1, "brightness": 50, "hue": 120, "saturation": 80, "color_temp": 0}
    }
    return device


def dimmer_device(client: KasaCloudClient) -> KasaCloudDevice:
    device = KasaCloudDevice(
        {
            "deviceId": "DIM1",
            "deviceType": "IOT.SMARTPLUGSWITCH",
            "deviceModel": "ES20M(US)",
            "appServerUrl": "https://use1-wap.tplinkcloud.com",
        },
        client,
    )
    device.sys_info = {"relay_state": 1, "brightness": 40}
    return device


# --------------------------------------------------------------------------
# Brightness must be scaled exactly once
# --------------------------------------------------------------------------

async def test_full_brightness_reaches_device_as_one_hundred_percent():
    """Regression: 0-255 was scaled to percent twice, capping output at 39%."""

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        return FakeResponse({"error_code": 0, "result": {}}, url=url)

    client, session = make_client(handler)
    await bulb_device(client).set_brightness(255)

    sent = session.request_data(-1)
    lighting = sent["smartlife.iot.smartbulb.lightingservice"]["transition_light_state"]
    assert lighting["brightness"] == 100


def test_brightness_property_uses_home_assistant_scale():
    client, _ = make_client(lambda url, payload: None)

    assert bulb_device(client).brightness == 128


def test_brightness_is_unknown_when_device_does_not_report_it():
    client, _ = make_client(lambda url, payload: None)

    assert hs300_device(client).brightness is None


async def test_dimmer_does_not_receive_bulb_commands():
    """A wall dimmer has no lightingservice; sending one is a silent no-op."""

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        return FakeResponse({"error_code": 0, "result": {}}, url=url)

    client, session = make_client(handler)
    await dimmer_device(client).set_brightness(255)

    commands = [
        r["json"]["params"]["requestData"]
        for r in session.requests
        if r["json"]["method"] == "passthrough"
    ]
    assert any("smartlife.iot.dimmer" in c for c in commands)
    assert not any("smartbulb" in c for c in commands)


# --------------------------------------------------------------------------
# Colour
# --------------------------------------------------------------------------

def test_hsv_reads_light_state():
    client, _ = make_client(lambda url, payload: None)

    assert bulb_device(client).hsv == (120, 80, 50)


def test_hsv_is_none_for_a_plug():
    """light.py called .hsv unconditionally; the attribute did not exist."""
    client, _ = make_client(lambda url, payload: None)

    assert hs300_device(client).hsv is None


def test_color_temp_is_none_when_device_reports_zero():
    client, _ = make_client(lambda url, payload: None)

    assert bulb_device(client).color_temp is None


# --------------------------------------------------------------------------
# "Unknown" must not be dressed up as a real reading
# --------------------------------------------------------------------------

def test_led_status_is_unknown_when_not_reported():
    client, _ = make_client(lambda url, payload: None)

    assert hs300_device(client).led_status is None


def test_overheated_is_unknown_when_not_reported():
    """Upstream reported a confident 'OK' on devices with no thermal sensor."""
    client, _ = make_client(lambda url, payload: None)

    assert hs300_device(client).overheated is None


def test_connectivity_is_unknown_when_cloud_omits_status():
    client, _ = make_client(lambda url, payload: None)
    device = KasaCloudDevice({"deviceId": "X"}, client)

    assert device.is_connected is None


# --------------------------------------------------------------------------
# Single-outlet emeter
# --------------------------------------------------------------------------

async def test_single_outlet_emeter_is_fetched_without_child_context():
    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        command = json.loads(payload["params"]["requestData"])
        if "emeter" in command:
            assert "context" not in command
            return FakeResponse(emeter_response({"power_mw": 7300}), url=url)
        return FakeResponse(sysinfo_response({"relay_state": 1, "model": "KP115"}), url=url)

    client, _ = make_client(handler)
    device = KasaCloudDevice(
        {
            "deviceId": "KP1",
            "deviceType": "IOT.SMARTPLUGSWITCH",
            "deviceModel": "KP115(US)",
            "appServerUrl": "https://use1-wap.tplinkcloud.com",
        },
        client,
    )

    await device.update()

    assert device.emeter_realtime["power_mw"] == 7300


async def test_client_logs_in_once_across_many_polls():
    """HA owns the shared session, and a valid token is reused, not re-fetched."""

    def handler(url, payload):
        if payload["method"] == "login":
            return FakeResponse(LOGIN_OK, url=url)
        return FakeResponse(serve_device_call(payload), url=url)

    client, session = make_client(handler)
    device = hs300_device(client)

    await device.update()
    await device.update()
    await device.update()

    assert session.methods_called.count("login") == 1
