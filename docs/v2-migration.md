# Contingency plan: migrating to TP-Link's v2 cloud API

**Status: not started, and deliberately not scheduled.** This document exists so
the work can begin immediately if the legacy API stops working, not because
migrating now is a good idea. The reasoning for waiting is in
[Why not now](#why-not-now).

Last verified: 2026-08-14.

## Where things stand

This integration speaks TP-Link's legacy ("v1") cloud API: unsigned JSON POSTs
to `wap.tplinkcloud.com`, with a `passthrough` method wrapping the device
command as a JSON string.

TP-Link has deprecated that API in the sense that current Kasa app clients no
longer use it. It has **not** been switched off:

| Check (2026-08-14) | Result |
|---|---|
| `POST wap.tplinkcloud.com` with invalid credentials | `{"error_code":-20601,"msg":"Incorrect email or password"}` — alive and behaving |
| v1 TLS certificate | DigiCert, `*.tplinkcloud.com`, valid 2025-09-16 → 2026-10-17 |

A certificate renewed through late 2026 is weak but real evidence that the
endpoint is being maintained rather than wound down. No sunset date has been
published anywhere.

Re-run that check any time. No credentials are involved, since being *rejected*
is what proves the API is alive and still honouring the v1 contract:

```bash
curl -s -X POST 'https://wap.tplinkcloud.com/' \
  -H 'Content-Type: application/json' \
  -d '{"method":"login","params":{"appType":"Kasa_Android",
       "cloudUserName":"invalid@example.invalid","cloudPassword":"invalid",
       "terminalUUID":"00000000-0000-0000-0000-000000000000"}}'
```

| Response | Meaning |
|---|---|
| `-20601` "Incorrect email or password" | v1 is alive and honouring the v1 contract |
| `-23003` "App version is too old" | v1 may have been retired — this document applies |
| not JSON, or no response | the endpoint may no longer serve v1 |

In practice the integration itself is the alarm: since `-23003` now raises
`KasaCloudLegacyApiError`, a retirement surfaces in Home Assistant as a
re-authentication prompt whose message names the real cause, rather than as a
generic failure.

## What v2 requires

Verified directly where marked; otherwise from
[piekstra/tplink-cloud-api#82](https://github.com/piekstra/tplink-cloud-api/issues/82),
which is the only public write-up and is closed as done.

| Aspect | v1 (today) | v2 |
|---|---|---|
| Host | `wap.tplinkcloud.com` | `n-wap.tplinkcloud.com` |
| Login | `{"method":"login","params":{...}}` | `POST /api/v2/account/login`, flat body |
| `appType` | `Kasa_Android` | `Kasa_Android_Mix` |
| Request signing | none | HMAC-SHA1 on every request |
| Token | opaque, in `params.token` | access token + refresh token |
| Token renewal | full re-login | refresh token, auto on `-20651` |
| MFA / 2FA | impossible (`-23003`) | `-20677` + `getEmailVC4TerminalMFA` → `checkMFACodeAndLogin` |
| Regional endpoint | `appServerUrl` per device | `getAccountStatusAndUrl` |
| TLS | public CA (DigiCert) | **private CA** — see below |

### The signature

Per issue #82, `X-Authorization` carries a timestamp, nonce, access key and
signature, where the signature is HMAC-SHA1 over:

```
{base64(md5(request_body))}\n9999999999\n{uuid_nonce}\n{url_path}
```

The hardcoded `9999999999` in the timestamp slot suggests the server does not
validate it, which is convenient and also a sign of how little this is
understood.

### Two blockers, both verified

**1. The v2 endpoint uses a TP-Link private CA.** Confirmed locally:

```
$ openssl s_client -connect n-wap.tplinkcloud.com:443 -servername n-wap.tplinkcloud.com
issuer=DC=cn, DC=com, DC=tp-link, CN=TP-LINK CA P1
subject=C=US, L=Irvine, O=TP-LINK GLOBAL INC., CN=*.tplinkcloud.com
notBefore=Oct 28 00:26:51 2025 GMT
notAfter=Oct 28 00:26:51 2026 GMT

$ curl -s -o /dev/null -w '%{ssl_verify_result} %{http_code}' https://n-wap.tplinkcloud.com/
19 000
```

OpenSSL result 19 is "self-signed certificate in certificate chain", and
`http_code` 000 means no HTTP exchange happened at all. So a standard client
cannot reach v2. The options are:

- **Bundle TP-Link's CA and pin it to that host.** Defensible — pinning one CA
  for one host is arguably stronger than trusting the public root store — but it
  means shipping a third-party CA in a HACS integration and rotating it when it
  expires.
- **Disable TLS verification.** Not acceptable. This integration carries the
  user's TP-Link account password, and the audit that produced this fork
  specifically removed weaknesses of this class.

Any implementation must therefore scope the custom trust to
`n-wap.tplinkcloud.com` alone, never to the default session.

**2. The signing key comes from a decompiled APK.** Issue #82 states the secret
was obtained by decompiling the Kasa Android app. Consequences:

- TP-Link can rotate it and break every third-party client without notice.
- It means shipping a key extracted from someone else's application. That is a
  materially different posture from calling an undocumented HTTP endpoint, and
  is a decision for the repository owner, not a technical detail.

## Why not now

Migrating today would trade a working system for a more fragile one:

- **No functional gain.** v2's headline feature is MFA. The account this runs
  against has 2FA disabled, so it buys nothing. Refresh tokens are a nicety; the
  current client re-authenticates on expiry and that works.
- **Worse failure surface.** Adds a bundled private CA (leaf currently expiring
  2026-10-28) and an APK-extracted secret, both of which can break unilaterally.
- **v1 works.** Verified above, with a certificate renewed into late 2026.
- **The rewrite is not small.** Signing, MFA, refresh tokens, regional endpoint
  discovery and custom TLS trust, all against a protocol documented in exactly
  one GitHub issue.

## Open questions to answer first

1. **How does `piekstra/tplink-cloud-api` get past the private CA?** Its README
   does not mention a CA bundle or disabled verification, yet `curl` cannot
   complete a handshake. Either it bundles a cert, disables verification, or the
   CA is in some trust stores. This must be answered before adopting or copying
   it — if the answer is "disables verification", it is not a usable model.
2. **Is the signing key stable, or per-app-version?** Determines whether a v2
   client rots on every Kasa app release.
3. **Does v1 `passthrough` still work for accounts that authenticate via v2?**
   If so, a much smaller migration exists: v2 for login only, v1 for device
   control.
4. **Is there any officially sanctioned path?** TP-Link's own position is that
   Home Assistant is unsupported. Matter, SmartThings and Alexa/Google are the
   sanctioned routes, but none reach a device on a third-party network the way
   the cloud API does.

## If it has to happen

The client is already the only seam. `KasaCloudClient` exposes exactly three
methods to the rest of the integration:

```python
async def login(self) -> None
async def fetch_device_records(self) -> list[dict]
async def passthrough(self, device_id, command, app_url=None, context=None) -> dict
```

`KasaCloudDevice`, the coordinator, and every platform depend only on those.
Nothing above the client knows the wire format. So:

1. **Answer the open questions**, especially #1 and #3. Question 3 could reduce
   this to a login-only change.
2. **Decide the dependency question.** Wrapping `piekstra/tplink-cloud-api`
   (maintained, MFA and refresh handled) is almost certainly better than
   reimplementing signing here — it turns this into a dependency in
   `manifest.json` plus an adapter, and moves protocol maintenance to someone
   tracking it. Weigh that against adding a dependency that itself carries an
   APK-extracted secret.
3. **Write `cloud_api_v2.py`** implementing the same three methods. Do not
   modify `cloud_api.py`; keeping both allows a config option and a fallback.
4. **Scope the TLS trust.** A dedicated `aiohttp.TCPConnector` with an
   `SSLContext` trusting the bundled TP-Link CA, used *only* for that host.
   Never touch the shared Home Assistant session's default verification.
5. **Add MFA to the config flow** as an `async_step_mfa`, triggered by `-20677`.
6. **Port the test suite.** `tests/test_cloud_api.py` tests behaviour, not wire
   format — availability semantics, error taxonomy, child-outlet resolution,
   no-login-storm — so most of it should transfer with a new fake. Re-run
   `mutate.py` against the v2 client; every mutation should still be caught.
7. **Keep v1 selectable** until v2 is proven against real hardware.

## Alternatives worth preferring

If v1 dies, rewriting against a reverse-engineered signed API is not obviously
the best answer. A device on the remote network, if one can be placed there,
removes the cloud from the picture entirely:

- **Tailscale subnet router** on the remote LAN plus Home Assistant's built-in
  `tplink` integration — officially maintained, local, no account password, and
  no protocol archaeology. Needs a device you control on site, and the plug's IP
  to be stable.
- **A small on-site agent** using `python-kasa` locally, publishing to an MQTT
  broker you own. Solves the IP-stability problem too, since discovery happens
  on that LAN.

Both are strictly more durable than any cloud path. The cloud API only exists in
this project because no such device is available at the remote site.
