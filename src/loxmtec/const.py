"""Constants shared across the LOX-MTEC package."""

from __future__ import annotations

# Polling groups of the register map, in the order the poller cycles them.
GROUP_CONFIG = "config"
GROUP_DAY = "day"
GROUP_TOTAL = "total"
GROUP_NOW_BASE = "now-base"

# "now-*" groups are read round-robin, one per poll cycle, to keep Modbus
# traffic low while still refreshing the base group on every cycle.
NOW_EXTENDED_GROUPS = (
    "now-grid",
    "now-inverter",
    "now-backup",
    "now-battery",
    "now-pv",
)

# Loxone transport modes
MODE_OFF = "off"
MODE_UDP = "udp"
MODE_HTTP = "http"
LOXONE_MODES = (MODE_OFF, MODE_UDP, MODE_HTTP)

# Connection states reported by the GUI / health endpoint
STATE_OK = "ok"
STATE_WARN = "warn"
STATE_ERROR = "error"
STATE_UNKNOWN = "unknown"
STATE_DISABLED = "disabled"
