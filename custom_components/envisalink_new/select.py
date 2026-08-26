"""Support for the Envisalink zone-discovery mode selector.

Paired with the "Discover Zone Info" button in button.py: a button entity
can't ask the user anything when pressed, so this select entity is how the
Zone Scan device lets a UI click choose apply/remove_unused instead of only
being available by calling the discover_zone_info service directly with
those fields set.
"""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_PANEL_MODEL,
    DEFAULT_PANEL_MODEL,
    DEFAULT_ZONE_DISCOVERY_MODE,
    DOMAIN,
    PANEL_MODEL_VISTA_20P,
    ZONE_DISCOVERY_MODE_SELECT_SUFFIX,
    ZONE_DISCOVERY_MODES,
)
from .models import EnvisalinkZoneScanDevice
from .pyenvisalink.const import PANEL_TYPE_HONEYWELL


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the zone-discovery mode selector, Honeywell/Vista-20P only."""
    controller = hass.data[DOMAIN][entry.entry_id]
    panel_type = controller.controller.panel_type
    entities = []

    if panel_type == PANEL_TYPE_HONEYWELL and entry.data.get(
        CONF_PANEL_MODEL, DEFAULT_PANEL_MODEL
    ) == PANEL_MODEL_VISTA_20P:
        entities.append(
            EnvisalinkZoneDiscoveryModeSelect(
                controller,
                f"{controller.unique_id}_{ZONE_DISCOVERY_MODE_SELECT_SUFFIX}",
            )
        )

    async_add_entities(entities)


class EnvisalinkZoneDiscoveryModeSelect(EnvisalinkZoneScanDevice, SelectEntity):
    """Picks what the paired "Discover Zone Info" button does when pressed."""

    _attr_has_entity_name = True
    _attr_name = "Zone Discovery Mode"
    _attr_should_poll = False
    _attr_options = ZONE_DISCOVERY_MODES
    _attr_current_option = DEFAULT_ZONE_DISCOVERY_MODE

    def __init__(
        self,
        controller,
        unique_id: str,
    ) -> None:
        """Initialize the zone-discovery mode select entity."""
        self._attr_unique_id = unique_id
        super().__init__("Zone Discovery Mode", controller, None, None)

    async def async_select_option(self, option: str) -> None:
        """Store the selected mode for the discovery button to read."""
        self._attr_current_option = option
        self.async_write_ha_state()
