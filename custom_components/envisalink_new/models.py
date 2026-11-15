"""Models for Envisalink."""
from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN, LOGGER, ZONE_SCAN_DEVICE_SUFFIX

# HA 2026.10+ deprecates `via_device` (an identifier tuple) in favour of
# `via_device_id` (a device registry id); `via_device` stops working in 2027.8.
# Older releases only know `via_device`, so pick whichever DeviceInfo supports.
_SUPPORTS_VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__optional_keys__


def panel_device_info(controller) -> DeviceInfo:
    """Return the device info for the alarm panel's own device."""
    return DeviceInfo(
        identifiers={(DOMAIN, controller.unique_id)},
        name=controller.alarm_name,
        manufacturer="eyezon",
        model=(
            f"Envisalink {controller.controller.envisalink_version}: "
            f"{controller.controller.panel_type}"
        ),
        sw_version=controller.controller.firmware_version,
        hw_version=str(controller.controller.envisalink_version),
        configuration_url=f"http://{controller.controller.host}",
    )


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
        return panel_device_info(self._controller)

    @property
    def available(self) -> bool:
        """Return if this entity is available or not."""
        return self._controller.available and super().available


class EnvisalinkZoneScanDevice(EnvisalinkDevice):
    """Entities that belong to the separate 'Zone Scan' sub-device.

    Rendered as its own device card/bubble in the HA UI (linked back to the
    alarm panel's device via `via_device_id`) rather than living inside the
    alarm panel device's own Controls section. Only ever instantiated for
    Honeywell panels configured as a Vista-20P-compatible model (Vista-20P
    or Vista-21iP) -- see button.py/switch.py.
    """

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the Zone Scan sub-device."""
        if _SUPPORTS_VIA_DEVICE_ID and self._controller.device_id:
            parent = {"via_device_id": self._controller.device_id}
        else:
            parent = {"via_device": (DOMAIN, self._controller.unique_id)}
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._controller.unique_id}_{ZONE_SCAN_DEVICE_SUFFIX}")},
            name="Zone Scan",
            manufacturer="eyezon",
            model="Honeywell Vista-20P/21iP zone discovery",
            **parent,
        )
