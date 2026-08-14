# Changes from upstream

Fork point: `onoffautomations/hassio_kasa_cloud` @ `a568984` (2026-01-23).

Every item below was a defect found by audit of that commit. Line references
are to the upstream files.

## Security

**Session token could reach `home-assistant.log`.** The token was appended to
the request URL (`cloud_api.py:330`). `aiohttp.ClientResponseError.__str__`
embeds the request URL, and that exception was logged at ERROR
(`cloud_api.py:341`), stuffed into a returned dict and logged again at INFO
(`:370`, `:403`), then re-raised into `__init__.py:33` and
`config_flow.py:65`. HA's default log level is INFO, and `strings.json` told
users to check the logs.
*Fix:* the token is sent in the JSON request body (`params.token`), which the
v1 API accepts and which never appears in a URL, a proxy log, or
`ClientResponseError.__str__`. `_call` raises typed exceptions `from None` so
the original message cannot travel up the chain. No logging call interpolates a
response body or an exception object.
(An `Authorization: Bearer` header was tried first and reverted: the v1 API
does not read a header, so it authenticated nothing.)

**Redirects were followed with the request body intact.** A `307` from
anywhere in the TP-Link zone would have replayed the `login` body — the
cleartext password — to an unvalidated location, and aiohttp permits
downgrading to `http` on redirect. Redirect targets never passed through the
host allowlist at all.
*Fix:* `allow_redirects=False`, and a 3xx is an error.

**`appServerUrl` userinfo and port were unchecked.** A host such as
`https://someone@use1-wap.tplinkcloud.com` passed the suffix test, and aiohttp
converts userinfo into BasicAuth — raising an uncaught `ValueError` that broke
every poll.
*Fix:* userinfo and non-443 ports are rejected, and the URL is rebuilt from
validated components rather than passed through, dropping query and fragment.

**A single expiry caused one login per device.** Nothing serialised
re-authentication, so N devices refreshed concurrently with the same
credentials — and because the cloud invalidates a terminal's previous token on
re-login, most of those tokens were dead on arrival, which re-triggered the
cycle.
*Fix:* an `asyncio.Lock` plus a token generation counter, so a caller only logs
in if nobody else already refreshed.

**A refused token could loop forever.** If the cloud rejected even a freshly
issued token, the retry path re-authenticated indefinitely without ever
escalating.
*Fix:* a fresh token being refused raises `KasaCloudAuthError`, so Home
Assistant stops polling and asks for reauthentication. Consecutive login
failures are also capped.

**`appServerUrl` was trusted verbatim from the response body.** It was used as
the request target (`cloud_api.py:25` → `:327`) with no scheme or host check,
and the token was concatenated onto it — a token-exfiltration and SSRF path.
*Fix:* `_endpoint()` requires `https` and a host under `.tplinkcloud.com`,
falling back to the default endpoint and logging a warning otherwise.

**Credentials were re-submitted on every error.** Any non-zero `error_code` —
including the entirely normal "device is offline" — triggered a full password
login (`cloud_api.py:402-406`, `:369-372`) at a 5-second poll interval:
roughly 17,000 credential submissions per device per day, with no backoff.
TP-Link throttles and temporarily blocks accounts for exactly this.
*Fix:* re-login only on token-expiry codes, at most once per call; terminal
credential errors raise `KasaCloudAuthError` and stop polling via
`ConfigEntryAuthFailed`. Poll interval raised to 60s.

**Account email logged at INFO/WARNING** (`config_flow.py:51`, `:54`) and used
as a device-registry identifier (`__init__.py:62`).
*Fix:* removed from logs; the hub device is keyed on `entry_id`.

**Password copied onto the coordinator** (`__init__.py:103-104`) where it was
never read. *Fix:* removed; the coordinator holds the client.

**Unused dependency.** `python-kasa>=0.7.0` was declared but never imported,
so HA installed it and its transitive tree for nothing.
*Fix:* `requirements: []`, enforced by a test.

**Floating action refs.** `hacs/action@main` and
`home-assistant/actions/hassfest@master` execute whatever is on those branches.
*Fix:* pinned to commit SHAs.

**Installed artifact differed from source.** `hacs.json` set
`zip_release: true`, so HACS installed a release ZIP rather than the reviewed
tree — and the published ZIP's manifest was a commit behind `main`
(`"codeowners": "OnOff Automations"` as a bare string, not a list).
*Fix:* `zip_release` removed; installs come from source. The ZIP-building
workflow is deleted.

## Correctness

**Power-strip outlets froze at startup.** Child objects cached a dict from the
setup-time `sys_info`, which `update()` then replaced with a freshly parsed
dict each poll, orphaning them (`switch.py:37`, `cloud_api.py:90-97`, `:267`).
Outlet states never changed again for the life of the process.
*Fix:* `KasaCloudChildDevice.data` resolves against the parent's current
`sys_info` on every access.

**Failed commands reported success.** `turn_on`/`turn_off` discarded the
`passthrough` response entirely (`cloud_api.py:184`, `:191`), so a rejected
command returned cleanly and no automation could detect it.
*Fix:* `passthrough` raises on failure; platforms surface it as
`HomeAssistantError`. The envelope's `error_code` is only half the story — the
device reports its own `err_code` *inside* an otherwise successful response, so
that is checked too. Ignoring it left the original bug intact one layer down.

**Entities never went unavailable, and unknown state rendered as `off`.**
`update()` caught every exception (`cloud_api.py:174`), so the coordinator
could not fail and `available` stayed `True` forever while stale values were
displayed as live. With no state at all, `is_on` returned `False`.
*Fix:* `update()` clears cached state and re-raises; `is_on` returns `None`
when unknown; `KasaCloudEntity.available` requires real state.

**Setup failure was permanent.** `async_setup_entry` returned `False`
(`__init__.py:34`), which HA never retries — a WAN blip during startup
disabled the integration until a manual reload.
*Fix:* `ConfigEntryNotReady` for transport errors, `ConfigEntryAuthFailed`
for auth.

**No re-authentication.** A changed password bricked the entry with no UI path.
*Fix:* `async_step_reauth` / `async_step_reauth_confirm`.

**Every config-flow failure said "cannot connect"**, including a wrong password
(`config_flow.py:64-66`). The `try` also wrapped
`_abort_if_unique_id_configured()`, so re-adding a configured account reported
a connection error instead of `already_configured`.
*Fix:* distinct `invalid_auth` / `cannot_connect` / `no_devices_found`;
uniqueness check moved outside the error handling.

**Energy monitoring never worked.** All four sensors read
`self._device.emeter_realtime`, which existed nowhere, and `update()` never
requested emeter data. A bare `except Exception` (`sensor.py:100`) made this
silent, so the advertised feature produced four permanently blank sensors.
*Fix:* `_update_emeter()` fetches `emeter.get_realtime`, per outlet with a
`context.child_ids` on strips.

**Energy detection was wrong in both directions.** `"25" in model` matched
KL125 (a bulb with no meter), while "HS300" matched none of the substrings.
*Fix:* model prefix match plus the cloud's `feature` flag.

**Brightness was scaled twice.** `light.py:123` converted 0-255 to percent and
`cloud_api.py:195` converted again, capping the bulb at 39%.
*Fix:* `set_brightness` takes HA's 0-255 scale and converts once.

**`hs_color` raised.** `light.py:105` read a `hsv` attribute that did not
exist; line 132 guarded the same access with `hasattr`, permanently false.
*Fix:* `hsv` property added; both paths use it.

**Removed HA API.** `light.py` implemented the mired `color_temp` property,
removed in HA 2026.3, so colour temperature was simply absent.
*Fix:* `color_temp_kelvin`, plus explicit min/max Kelvin.

**Dimmers received bulb commands.** An ES20M matched both `is_plug` and
`is_dimmable`, got a switch but no light, and then received
`smartlife.iot.smartbulb.lightingservice` — a service it does not implement —
so it silently did nothing (`cloud_api.py:181`).
*Fix:* capabilities are derived from the reported `sys_info`, and dimmers use
`smartlife.iot.dimmer`.

**A partial cloud record killed whole platforms.** `device.model.lower()` and
`"Plug" in self.device_type` on a `None` field raised inside
`async_setup_entry` (`switch.py:51`, `binary_sensor.py:34`,
`cloud_api.py:51`), taking down every device's entities.
*Fix:* fields coerced to `str` at the boundary.

**Config-flow UI showed raw keys.** Only `strings.json` was shipped; HA reads
`translations/en.json` for custom integrations.
*Fix:* `translations/en.json` added, checked by a test.

**Cloud reachability was frozen.** `is_connected` read `device_info["status"]`
from the one-off `getDeviceList` at setup, which was never re-fetched.
*Fix:* the coordinator refreshes device records every 30 minutes.

**Base64 device names.** The cloud returns some aliases base64-encoded;
upstream passed them through raw, producing entities named
`RGlzaHdhc2hlcg==` (upstream issue #2, unresolved).
*Fix:* aliases are decoded when they round-trip to printable UTF-8.

**Non-canonical login `appType`.** `"Kasa"` diverges from every published
client. *Fix:* `"Kasa_Android"`.

**Terminal UUID regenerated every restart**, accumulating registered terminals
on the account. *Fix:* persisted in the config entry.

## Removed

- `KasaAutoUpdateSwitch` — `is_on` hardcoded `False`, `turn_on`/`turn_off`
  were `pass`. A visible toggle that did nothing.
- `KasaOnSinceSensor` — `native_value` returned `None` with a `# Placeholder`
  comment. A timestamp recomputed each poll also churns state needlessly.
- `KasaSignalLevelSensor` — redundant with the RSSI sensor.
- Motion entities — `get_sysinfo` does not carry `motion_detected`, so the
  sensor was always `False`, and the enable switch used an unverified service.
- The synthetic "Main Power" strip switch — no master relay exists in hardware,
  so it could not report a truthful state, and its `unique_id` embedded a
  display name.
- Per-request `aiohttp.ClientSession` — replaced with HA's shared session.

## Added

- `coordinator.py` and `entity.py`. The `device_info` dict was previously
  duplicated in eight places across five files with three drifted variants of
  the `sw_version` guard.
- `PARALLEL_UPDATES` on write platforms, to serialise calls to a rate-limited
  API.
- Energy polling decoupled from state polling (`EMETER_REFRESH_INTERVAL`).
  Energy costs one cloud call per outlet, so folding it into every state poll
  made a 6-outlet HS300 seven calls a minute. Energy reads are also non-fatal:
  one flaky reading no longer discards the state that was just fetched
  successfully, nor blocks setup.
- A `unique_id` backfill for entries created by the upstream version, which had
  none — without it the same account could be configured twice.
- Explicit device registration before platform setup. Platforms are set up
  concurrently, so a child outlet registered with `via_device` could otherwise
  land before its strip existed and end up unlinked.
- A test suite (81 tests). Upstream had none, and its CI validated only
  metadata, so no code had ever been executed by an automated check. Every
  test was verified to fail when its corresponding bug is reintroduced — 23
  mutations, all caught.

## Known residual risks

- Only two TP-Link error codes are corroborated by public sources (`-20601`
  wrong password, `-20651` token expired). The rest of `TOKEN_ERROR_CODES` and
  `CREDENTIAL_ERROR_CODES` are best-effort. A missing code degrades to a
  generic error and a retry, not a storm, because a refused fresh token
  escalates to a re-auth prompt.
- The host allowlist trusts the whole `tplinkcloud.com` zone, so a hijacked or
  stale subdomain would still receive the token. Narrowing it risks rejecting a
  legitimate regional endpoint and losing device control, so the zone check
  stands.
- No response body size limit on `response.json()`.
- The coordinator fails the whole cycle if any device raises something that is
  not a `KasaCloudError`, on the grounds that such an error is a bug here and
  should not be hidden.
