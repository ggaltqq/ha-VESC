"""Constants for the vesc integration."""

DOMAIN = "vesc_express"

CONF_CONTROLLER_CAN_ID = "controller_can_id"
CONF_BMS_CAN_ID = "bms_can_id"
CONF_POLL_INTERVAL = "poll_interval"
CONF_CELL_TYPE = "cell_type"

DEFAULT_PORT = 65102
DEFAULT_POLL_INTERVAL = 20  # seconds
DEFAULT_TIMEOUT = 15.0  # seconds per TCP read attempt
DEFAULT_RETRIES = 3  # the Express TCP bridge resets sockets transiently

# How often (in poll cycles) to re-run COMM_PING_CAN to refresh the cached CAN
# node list. The list is also refreshed whenever it is empty.
STATIC_REFRESH_EVERY_POLLS = 30

# --- VESC binary protocol opcodes ---------------------------------------
# These four are part of the original, long-stable VESC COMM_PACKET_ID
# enum and have not changed across firmware generations:
COMM_FW_VERSION = 0
COMM_GET_VALUES = 4
COMM_GET_VALUES_SETUP = 47  # includes the persistent odometer (meters)
COMM_FORWARD_CAN = 34

# CAN ping + BMS telemetry opcodes. Confirmed against real VESC Express
# hardware (FW 6.5). If a future firmware branch renumbers these, BMS/CAN reads
# will simply fail rather than do anything unsafe, since this integration never
# sends writes.
COMM_PING_CAN = 62
COMM_BMS_GET_VALUES = 96
