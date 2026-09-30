"""Integration constants."""

DOMAIN = "ekd_ampere_iq"
CONF_CONNECTION_TYPE = "connection_type"
CONNECTION_CLOUD = "cloud"
CONNECTION_MODBUS = "modbus"
CONF_MODBUS_HOST = "host"
CONF_MODBUS_PORT = "port"
CONF_MODBUS_UNIT_ID = "unit_id"
CONF_INSTALLATION_UUID = "installation_uuid"
CONF_EQUIPMENT = "equipment"
CONF_POLL_INTERVAL = "poll_interval"
CONF_PV_SOURCES = "pv_sources"
CONF_PV_TOPOLOGY = "pv_topology"
PV_TOPOLOGY_SINGLE = "single"
PV_TOPOLOGY_MULTIPLE = "multiple"
DEFAULT_PV_SOURCES = ("e3_total",)
EQUIPMENT = ("inverter", "battery", "heat_pump", "wallbox")
MODBUS_EQUIPMENT = ("inverter", "battery")
DEFAULT_POLL_INTERVAL = 60
