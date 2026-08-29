"""Models for Envisalink."""
from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN, LOGGER, ZONE_SCAN_DEVICE_SUFFIX


class EnvisalinkDevice(Entity):
    """Representation of an Envisalink device."""

    def __init__(self, name, controller, state_update_type, state_update_key):
        """Initialize the device."""
        self._controller = controller
        self._attr_should_poll = False
        self._attr_name = name
        self._state_update_type = state_update_type
        self._state_update_key = state_update_key

    async def async_added_to_hass(self) -> None:
        """Register this entity to receive state change updates from the underlying device."""

        def state_updated():
            LOGGER.debug("state_updated for '%s'", self._attr_name)
            self.async_write_ha_state()

        if self._state_update_type and self._state_update_key:
            self.async_on_remove(
                self._controller.add_state_change_listener(
                    self._state_update_type, self._state_update_key, state_updated
                )
            )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information about this EVL device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._controller.unique_id)},
            name=self._controller.alarm_name,
            manufacturer="eyezon",
            model=(
                f"Envisalink {self._controller.controller.envisalink_version}: "
                f"{self._controller.controller.panel_type}"
            ),
            sw_version=self._controller.controller.firmware_version,
            hw_version=self._controller.controller.envisalink_version,
            configuration_url=f"http://{self._controller.controller.host}",
        )

    @property
    def available(self) -> bool:
        """Return if this entity is available or not."""
        return self._controller.available and super().available


class EnvisalinkZoneScanDevice(EnvisalinkDevice):
    """Entities that belong to the separate 'Zone Scan' sub-device.

    Rendered as its own device card/bubble in the HA UI (linked back to the
    alarm panel's device via `via_device`) rather than living inside the
    alarm panel device's own Controls section. Only ever instantiated for
    Honeywell panels configured as a Vista-20P-compatible model (Vista-20P
    or Vista-21iP) -- see button.py/switch.py.
    """

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the Zone Scan sub-device."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._controller.unique_id}_{ZONE_SCAN_DEVICE_SUFFIX}")},
            name="Zone Scan",
            manufacturer="eyezon",
            model="Honeywell Vista-20P/21iP zone discovery",
            via_device=(DOMAIN, self._controller.unique_id),
        )
