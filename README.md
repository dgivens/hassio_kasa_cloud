# TP-Link Kasa Cloud for Home Assistant

Control TP-Link **Kasa** devices through TP-Link's cloud API, for devices Home
Assistant cannot reach on the local network — for example a plug or power strip
at a remote site on someone else's network.

If your devices *are* reachable locally, use the built-in
[TP-Link Smart Home integration](https://www.home-assistant.io/integrations/tplink/)
instead. It is officially maintained, talks to devices locally, and does not
need your account password. This integration exists only for the remote case.

This is a fork of [onoffautomations/hassio_kasa_cloud](https://github.com/onoffautomations/hassio_kasa_cloud)
with security and correctness fixes. See [CHANGES.md](CHANGES.md) for what
differs and why.

## Read this before installing

- **Your TP-Link account password is stored in Home Assistant** in
  `.storage/core.config_entries`, in cleartext, and is included in every
  Home Assistant backup. That is how all HA cloud integrations work, but the
  Kasa cloud API takes the account password directly — there is no scoped
  token — so a compromise of your HA host or a backup exposes the whole
  TP-Link account.
- **Two-step verification is not supported.** The legacy cloud API this uses
  has no programmatic path for it; login fails with "App version is too old".
- **This uses TP-Link's legacy ("v1") cloud API, which TP-Link has
  deprecated.** It works today — verified, and its TLS certificate is renewed
  into late 2026 — but current Kasa app clients use a newer signed API. Expect
  this to stop working eventually, with no warning. A weekly CI job watches for
  it, and [docs/v2-migration.md](docs/v2-migration.md) records what a migration
  would involve and why it is not worth doing pre-emptively.
- **Nothing here is officially supported by TP-Link**, and the API is
  undocumented and rate-limited. Devices poll every 60 seconds by default.
  Lowering that risks your account being throttled or temporarily blocked.

## How this fork was produced

The audit of the upstream code and the changes in this fork were written with
[Claude](https://claude.com/claude-code) (Anthropic), directed and reviewed by
the repository owner. Commits carry `Co-Authored-By: Claude` trailers.

Specifically, that means:

- The upstream code was reviewed for security, correctness and protocol
  conformance, and the findings were checked against public reverse-engineering
  of the TP-Link cloud API rather than taken on trust. `CHANGES.md` maps every
  fix to the upstream line it addresses.
- The rewrite was reviewed again afterwards by separate agents. That pass caught
  a real regression — the session token had been moved to an `Authorization`
  header the API does not read, which would have broken every request — so the
  process was not merely self-confirming, but it also demonstrates that the
  first pass shipped a serious mistake.
- There are 90 automated tests where upstream had none. Each was verified to
  fail when the bug it covers is reintroduced (23 mutations, all caught), so the
  suite is known to detect regressions rather than merely passing.

What that does **not** amount to:

- No independent human security audit. If you are installing this, read
  `custom_components/kasa_cloud/cloud_api.py` yourself — it is the file that
  handles your credentials, and it is under 800 lines.
- The tests exercise a fake cloud, not TP-Link's. They prove internal
  consistency, not that the protocol assumptions are correct. The protocol is
  undocumented and reverse-engineered.
- Real-hardware testing covers one HS300 power strip on one account. Bulbs,
  dimmers, wall switches and single plugs are implemented but unverified against
  physical devices.

## Installation

### HACS (custom repository)

1. HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/dgivens/hassio_kasa_cloud`, category **Integration**
3. Install, then restart Home Assistant

Installs are made from the repository source, so what runs is what you can read
here. (Upstream shipped a release ZIP, meaning the installed bytes were a
separate artifact from the reviewed source.)

### Manual

```bash
git clone https://github.com/dgivens/hassio_kasa_cloud
rsync -a hassio_kasa_cloud/custom_components/kasa_cloud/ \
  /path/to/homeassistant/config/custom_components/kasa_cloud/
```

Copy only `custom_components/kasa_cloud` — the rest of the repository is tests,
CI and brand assets that Home Assistant has no use for. Restart Home Assistant
afterwards.

## Setup

Settings → Devices & Services → Add Integration → **TP-Link Kasa Cloud**, then
enter your TP-Link account email and password. Devices in the account are
discovered automatically. If the password later changes, Home Assistant will
prompt you to re-authenticate rather than silently failing.

## What you get

| Platform | Entities |
|---|---|
| `switch` | One per plug or wall switch; one per outlet on a power strip. Status LED toggle where the device reports one. |
| `sensor` | Energy monitoring (power, voltage, current, total) on metered hardware — per outlet on an HS300. Wi-Fi signal strength (disabled by default). |
| `binary_sensor` | Cloud reachability; overheat state where the device reports it. |
| `button` | Reboot (disabled by default — on a strip it power-cycles every outlet). |
| `light` | Bulbs and wall dimmers: on/off, brightness, colour temperature, colour. |

Entities are only created for capabilities a device actually reports, so you
should not see sensors that can never have a value.

### Power strips

An HS300 or KP303 appears as the strip itself plus one device per outlet, each
nested under the strip (visible as "Connected devices" on the strip's page).
Each outlet has its own switch, and on the HS300 its own energy sensors; the
strip holds the LED toggle, cloud-connection sensor and reboot button.

This mirrors how Home Assistant's built-in `tplink` integration models the
HS300, and it is what lets you assign each outlet to a different Area — useful
when one outlet powers a mount and another a camera. The trade-off is more
entries in the device list.

There is deliberately no synthetic "all outlets" switch: the hardware has no
master relay, so such an entity cannot report a truthful state when outlets
differ. To switch everything at once, target the outlet switches from a script
or a group.

## Known limitations

- **Only Kasa plugs, wall switches, dimmers, strips and bulbs appear.** Your
  TP-Link account may also hold Tapo devices and cameras — the cloud returns
  them all — but they use a different protocol that this integration cannot
  drive, so they are filtered out rather than added as devices that could never
  work.
- Cloud polling means state changes made outside Home Assistant take up to
  60 seconds to appear. There is no push.
- Energy readings on a strip cost one cloud call per outlet per poll.
- If the cloud cannot be reached, entities go **unavailable** rather than
  showing their last known value. This is deliberate: a stale "off" on a
  remote outlet is worse than an honest "unknown".

## Development

```bash
python -m venv .venv
.venv/bin/pip install pytest pytest-asyncio homeassistant
.venv/bin/pytest
```

## License

Contributions in this fork are licensed under [Apache-2.0](LICENSE), matching
Home Assistant core.

The upstream repository this forks carries **no license**, so the upstream
author retains all rights to whatever of their work remains. [NOTICE](NOTICE)
records that in full, along with a reproducible measurement of how much does:
300 of 1,858 lines are byte-identical to upstream, and of those, 41% are blank
lines and the rest are almost entirely imports, decorators and method signatures
that Home Assistant's API dictates. Essentially none of upstream's original
expression survives, but the position is stated plainly rather than papered over.

## Disclaimer

Not affiliated with or endorsed by TP-Link. Use at your own risk.
