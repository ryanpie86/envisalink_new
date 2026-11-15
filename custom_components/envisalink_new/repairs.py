"""Repairs for Z-Wave JS."""

from __future__ import annotations

from homeassistant import data_entry_flow
from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, LOGGER, ZONE_SCAN_DEVICE_SUFFIX


class MigrateUniqueIDFlow(RepairsFlow):
    """Handler for an issue fixing flow."""

    def __init__(self, data: dict[str, str]) -> None:
        """Initialize."""
        self.description_placeholders: dict[str, str] = {
            "config_entry_title": data["config_entry_title"],
            "new_unique_id": data["new_unique_id"],
            "old_unique_id": data["old_unique_id"],
        }
        self._config_entry_id: str = data["config_entry_id"]

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        """Handle the first step of a fix flow."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        """Handle the confirm step of a fix flow."""
        if user_input is not None:
            config_entry = self.hass.config_entries.async_get_entry(self._config_entry_id)
            # If config entry was removed, we can ignore the issue.
            if config_entry is not None:
                self.hass.config_entries.async_update_entry(
                    config_entry,
                    unique_id=self.description_placeholders["new_unique_id"],
                )

                await self._async_update_entities()

                self.hass.config_entries.async_schedule_reload(config_entry.entry_id)
            return self.async_create_entry(data={})

        return self.async_show_form(
            step_id="confirm",
            description_placeholders=self.description_placeholders,
        )

    async def _async_update_entities(self):
        """Update the unique ID on all the entities and devices attached to this config
        entry.
        """

        old_unique_id = self.description_placeholders["old_unique_id"]
        new_unique_id = self.description_placeholders["new_unique_id"]

        @callback
        def _async_update_unique_id(
            entity_entry: er.RegistryEntry,
        ) -> dict[str, str] | None:
            id_parts = entity_entry.unique_id.split("_")
            if id_parts[0] == old_unique_id:
                id_parts[0] = new_unique_id
                return {"new_unique_id": "_".join(id_parts)}

        await er.async_migrate_entries(
            self.hass, self._config_entry_id, _async_update_unique_id
        )

        # Move the panel device and its Zone Scan sub-device to the new unique ID
        # rather than removing them. Removing a device deletes its entities' registry
        # entries, and HA before 2025.7 doesn't restore their settings when they're
        # recreated; keeping the devices also keeps their ids, names and areas.
        dev_reg = dr.async_get(self.hass)
        for old_id, new_id in (
            (old_unique_id, new_unique_id),
            (
                f"{old_unique_id}_{ZONE_SCAN_DEVICE_SUFFIX}",
                f"{new_unique_id}_{ZONE_SCAN_DEVICE_SUFFIX}",
            ),
        ):
            old_device = _async_get_device(dev_reg, (DOMAIN, old_id), self._config_entry_id)
            if not old_device:
                continue
            if _async_get_device(dev_reg, (DOMAIN, new_id), self._config_entry_id):
                # A device already exists under the new ID, so the old one is orphaned
                dev_reg.async_remove_device(old_device.id)
            else:
                dev_reg.async_update_device(old_device.id, new_identifiers={(DOMAIN, new_id)})


@callback
def _async_get_device(
    dev_reg: dr.DeviceRegistry, identifier: tuple[str, str], config_entry_id: str
) -> dr.DeviceEntry | None:
    # HA 2026.10 deprecates async_get_device (identifiers are no longer unique across
    # config entries) in favour of async_get_device_by_identifier; async_get_device
    # stops working in 2027.8. Older releases only have async_get_device.
    if hasattr(dev_reg, "async_get_device_by_identifier"):
        return dev_reg.async_get_device_by_identifier(identifier, config_entry_id)
    return dev_reg.async_get_device(identifiers={identifier})


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str] | None
) -> RepairsFlow:
    """Create fix flow."""

    if issue_id.split(".")[0] == "migrate_unique_id":
        assert data
        return MigrateUniqueIDFlow(data)
    return ConfirmRepairFlow()
