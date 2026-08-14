"""Constants for the TP-Link Kasa Cloud integration."""

DOMAIN = "kasa_cloud"

# Persisted so the account does not register a new "terminal" on every restart.
CONF_TERMINAL_UUID = "terminal_uuid"

# Seconds between device polls. This is a rate-limited consumer cloud API, not
# a local device: TP-Link is known to throttle and temporarily blacklist
# accounts that poll aggressively, and published guidance is minutes, not
# seconds. Upstream shipped 5, i.e. ~17k calls per device per day.
UPDATE_INTERVAL = 60

# The device *list* only changes when hardware is added or removed, but it is
# also the only source of cloud reachability, so refresh it periodically.
DEVICE_LIST_REFRESH_INTERVAL = 1800

# Energy readings cost one extra cloud call per outlet, so a 6-outlet HS300
# would otherwise turn one state poll into seven. Five-minute resolution is
# ample for the energy dashboard.
EMETER_REFRESH_INTERVAL = 300
