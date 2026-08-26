"""Support for Envisalink panic buttons and the Honeywell zone-discovery button."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_PANEL_MODEL,
    CONF_PARTITION_SET,
    DEFAULT_PANEL_MODEL,
    DEFAULT_PARTITION_SET,
    DOMAIN,
    LOGGER,
    PANEL_MODEL_VISTA_20P,
)
from .helpers import parse_range_string
from .models import EnvisalinkDevice
from .pyenvisalink.const import PANEL_TYPE_DSC, PANEL_TYPE_HONEYWELL, PANEL_TYPE_UNO
from .zone_discovery import async_run_zone_discovery

_panel_buttons = [
    {
        "type": "Fire",
        "label": {
            PANEL_TYPE_DSC: "Fire",
            PANEL_TYPE_HONEYWELL: "A",
            PANEL_TYPE_UNO: "Fire",
        },
    },
    {
        "type": "Ambulance",
        "label": {
            PANEL_TYPE_DSC: "Ambulance",
            PANEL_TYPE_HONEYWELL: "B",
            PANEL_TYPE_UNO: "Medical Emergency",
        },
    },
    {
        "type": "Police",
        "label": {
            PANEL_TYPE_DSC: "Police",
            PANEL_TYPE_HONEYWELL: "C",
            PANEL_TYPE_UNO: "Silent Police Panic",
        },
    },
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the panic buttons."""
    controller = hass.data[DOMAIN][entry.entry_id]
    panel_type = controller.controller.panel_type
    entities = []

    for button_info in _panel_buttons:
        label = button_info["label"].get(panel_type)
        if label:
            button = EnvisalinkPanicButton(
                controller,
                f"{controller.unique_id}_{label}",
                f"Panic {label}",
                button_info["type"],
            )
            entities.append(button)

    # Zone discovery is only implemented for Honeywell/Vista panels, and
    # only validated so far against a Vista-20P (see zone_discovery.py /
    # docs/zone_discovery.md). Gate on the configured panel model too, not
    # just panel_type, so a future non-Vista-20P model doesn't get a button
    # for a discovery flow it doesn't actually support yet.
    if panel_type == PANEL_TYPE_HONEYWELL and entry.data.get(
        CONF_PANEL_MODEL, DEFAULT_PANEL_MODEL
    ) == PANEL_MODEL_VISTA_20P:
        entities.append(
            EnvisalinkZoneDiscoveryButton(
                controller,
                f"{controller.unique_id}_discover_zone_info",
                entry,
            )
        )

    async_add_entities(entities)


class EnvisalinkPanicButton(EnvisalinkDevice, ButtonEntity):
    """Representation of a demo button entity."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _panic_type = None

    def __init__(
        self,
        controller: EnvisalinkController,
        unique_id: str,
        name: str,
        panic_type: str,
    ) -> None:
        """Initialize the panic button entity."""
        self._attr_unique_id = unique_id
        self._panic_type = panic_type
        super().__init__(name, controller, None, None)

    async def async_press(self) -> None:
        """Send out a persistent notification."""
        await self._controller.controller.panic_alarm(self._panic_type)


class EnvisalinkZoneDiscoveryButton(EnvisalinkDevice, ButtonEntity):
    """Button that runs a Honeywell/Vista zone-type discovery dry run.

    This always runs as a dry run (apply=False, remove_unused=False) --
    button entities in the HA UI can't prompt for parameters, so there's no
    way to offer apply/remove_unused as options here. Results are posted as
    a persistent notification, same as calling the `discover_zone_info`
    service with apply: false. To actually write the results into this
    integration's configuration (or to also remove unused zones), use the
    `envisalink_new.discover_zone_info` action from Developer Tools with
    apply: true (and remove_unused: true if wanted) instead -- this button
    is just a convenient way to preview what a scan would find.
    """

    _attr_has_entity_name = True
    _attr_name = "Discover Zone Info"
    _attr_should_poll = False
    _attr_entity_registry_enabled_default = True

    def __init__(
        self,
        controller: EnvisalinkController,
        unique_id: str,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the zone discovery button entity."""
        self._attr_unique_id = unique_id
        self._entry = entry
        super().__init__("Discover Zone Info", controller, None, None)

    async def async_press(self) -> None:
        """Run a zone-discovery dry run and post the results."""
        partition_spec = self._entry.data.get(CONF_PARTITION_SET, DEFAULT_PARTITION_SET)
        partition_set = parse_range_string(
            partition_spec, min_val=1, max_val=self._controller.controller.max_partitions
        )
        partition_number = partition_set[0] if partition_set else 1

        try:
            await async_run_zone_discovery(
                self.hass,
                self._controller,
                self._entry,
                partition_number,
                apply=False,
                remove_unused=False,
            )
        except HomeAssistantError as err:
            LOGGER.error("Zone discovery (button) failed: %s", err)
            raise
