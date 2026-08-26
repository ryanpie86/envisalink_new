"""Constants for the Envisalink integration."""

import logging

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from .pyenvisalink.const import PANEL_TYPE_DSC, PANEL_TYPE_HONEYWELL, PANEL_TYPE_UNO

DOMAIN = "envisalink_new"

LOGGER = logging.getLogger(__package__)

CONF_ALARM_NAME = "alarm_name"
CONF_ZONE_SET = "zone_set"
CONF_PARTITION_SET = "partition_set"

CONF_EVL_KEEPALIVE = "keepalive_interval"  # OPTION
CONF_EVL_PORT = "port"
CONF_EVL_DISCOVERY_PORT = "discovery_port"
CONF_EVL_VERSION = "evl_version"
CONF_PANEL_TYPE = "panel_type"
CONF_PANIC = "panic_type"  # OPTION
CONF_PASS = "password"
CONF_USERNAME = "user_name"
CONF_ZONEDUMP_INTERVAL = "zonedump_interval"  # OPTION
CONF_CREATE_ZONE_BYPASS_SWITCHES = "create_zone_bypass_switches"  # OPTION
CONF_HONEYWELL_ARM_NIGHT_MODE = "honeywell_arm_night_mode"  # OPTION
CONF_WIRELESS_ZONE_SET = "wireless_zone_set"
CONF_SHOW_KEYPAD = "show_keypad"
CONF_CODE_ARM_REQUIRED = "code_arm_required"
CONF_PARTITION_ASSIGNMENTS = "partition_assignments"
# Installer code, used only by the Honeywell zone-discovery service to read
# back existing zone names/types from the panel's own programming. Stored
# the same way (and with the same lack of extra-at-rest protection beyond
# HA's own config entry storage) as the end-user alarm CONF_CODE above --
# see docs/zone_discovery.md for why this is needed and what it's used for.
CONF_INSTALLER_CODE = "installer_code"
# Which physical panel model the zone-discovery feature should target --
# the *56 keystroke sequence, valid zone-number range, and zone-type table
# it uses are all specific to a panel model/family. Only one option exists
# today (Vista-20P, which this feature was built and hardware-validated
# against); this is here so a future revision can add other models without
# a breaking config change. See claude/envisalink-zone-discovery.md (or
# docs/zone_discovery.md) "Known open items" for the plan.
CONF_PANEL_MODEL = "panel_model"


# Config items used only in the YAML config
CONF_ZONENAME = "name"
CONF_ZONES = "zones"
CONF_ZONETYPE = "type"
CONF_PARTITIONNAME = "name"
CONF_PARTITIONS = "partitions"

# Temporary config entry key used to store values from the YAML config that will
# transition into the ConfigEntry options
CONF_YAML_OPTIONS = "yaml_options"

HONEYWELL_ARM_MODE_INSTANT_VALUE = "7"
HONEYWELL_ARM_MODE_NIGHT_VALUE = "33"

# Panel model values for CONF_PANEL_MODEL. "Non-ADT" because ADT-branded
# Vista panels use a locked-down installer code/menu structure that this
# feature has not been validated against.
PANEL_MODEL_VISTA_20P = "vista_20p"

SHOW_KEYPAD_NEVER_VALUE = "never"
SHOW_KEYPAD_DISARM_VALUE = "disarm"
SHOW_KEYPAD_ALWAYS_VALUE = "always"

DEFAULT_ALARM_NAME = "Alarm"
DEFAULT_CREATE_ZONE_BYPASS_SWITCHES = False
DEFAULT_EVL_VERSION = 4
DEFAULT_KEEPALIVE = 60
DEFAULT_ZONE_SET = ""
DEFAULT_PARTITION_SET = "1"
DEFAULT_PANIC = "Police"
DEFAULT_PORT = 4025
DEFAULT_DISCOVERY_PORT = 80
DEFAULT_TIMEOUT = 10
DEFAULT_USERNAME = "user"
DEFAULT_ZONEDUMP_INTERVAL = 30
DEFAULT_ZONETYPE = BinarySensorDeviceClass.OPENING
DEFAULT_HONEYWELL_ARM_NIGHT_MODE = HONEYWELL_ARM_MODE_NIGHT_VALUE
DEFAULT_SHOW_KEYPAD = SHOW_KEYPAD_ALWAYS_VALUE
DEFAULT_PANEL_MODEL = PANEL_MODEL_VISTA_20P

DEFAULT_CODE_ARM_REQUIRED = {
    PANEL_TYPE_DSC: False,
    PANEL_TYPE_HONEYWELL: True,
    PANEL_TYPE_UNO: False,
    None: True,
}
