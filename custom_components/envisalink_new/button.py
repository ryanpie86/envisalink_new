"""Support for Envisalink panic buttons and the Honeywell zone-discovery button."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_PANEL_MODEL,
    CONF_PARTITION_SET,
    DEFAULT_PANEL_MODEL,
    DEFAULT_PARTITION_SET,
    DOMAIN,
    LOGGER,
    PANEL_MODEL_VISTA_20P,
    ZONE_DISCOVERY_APPLY_SUFFIX,
    ZONE_DISCOVERY_INCLUDE_NAMES_SUFFIX,
    ZONE_DISCOVERY_REMOVE_UNUSED_SUFFIX,
)
from .helpers import parse_range_string
from .models import EnvisalinkDevice, EnvisalinkZoneScanDevice
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
    # for a discovery flow it doesn't actually support yet. Lives on the
    # separate "Zone Scan" device (see models.EnvisalinkZoneScanDevice), not
    # the alarm panel's own device, alongside the paired toggle switches
    # (switch.py).
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


class EnvisalinkZoneDiscoveryButton(EnvisalinkZoneScanDevice, ButtonEntity):
    """Button that runs a Honeywell/Vista zone-discovery scan.

    Lives on the "Zone Scan" sub-device next to three paired toggle
    switches (switch.py): Apply Changes, Include Names, Remove Unused
    Zones. A button entity can't prompt for apply/include_names/
    remove_unused when pressed, so instead this reads whatever those three
    switches are currently set to at press time -- flip the ones you want
    first, then press this button to run it. With everything off (the
    default) this is a no-op preview. Results are posted as a persistent
    notification either way. The `envisalink_new.discover_zone_info`
    service remains available too, for automations/scripts that want to
    set those fields directly without touching the switches.
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

    def _switch_is_on(self, suffix: str) -> bool:
        """Look up one paired toggle switch's current state by suffix."""
        registry = er.async_get(self.hass)
        unique_id = f"{self._controller.unique_id}_{suffix}"
        entity_id = registry.async_get_entity_id("switch", DOMAIN, unique_id)
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        return bool(state) and state.state == "on"

    async def async_press(self) -> None:
        """Run zone discovery using whatever the toggle switches are set to."""
        apply = self._switch_is_on(ZONE_DISCOVERY_APPLY_SUFFIX)
        include_names = self._switch_is_on(ZONE_DISCOVERY_INCLUDE_NAMES_SUFFIX)
        remove_unused = self._switch_is_on(ZONE_DISCOVERY_REMOVE_UNUSED_SUFFIX)

        # Include Names works independently of Apply Changes on purpose --
        # since *82 name reading is not yet hardware-validated the way *56
        # zone type is (see docs/zone_discovery.md), turning it on with
        # Apply Changes off is the safe way to preview what it reads
        # before ever writing it anywhere. Remove Unused Zones is
        # different: async_run_zone_discovery actively rejects
        # remove_unused=True without apply=True (there's nothing to remove
        # on a dry run), so silently drop it here rather than letting a
        # button press raise on that combination.
        if not apply:
            remove_unused = False

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
                apply=apply,
                remove_unused=remove_unused,
                include_names=include_names,
            )
        except HomeAssistantError as err:
            LOGGER.error("Zone discovery (button) failed: %s", err)
            raise
