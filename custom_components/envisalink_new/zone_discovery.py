"""Shared logic for running Honeywell/Vista zone-type discovery.

This is used both by the `discover_zone_info` entity service (see
alarm_control_panel.py) and by the "Discover Zone Info" button entity (see
button.py) so the two call sites can't drift out of sync. See
docs/zone_discovery.md for the full reasoning and safety model.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_INSTALLER_CODE,
    CONF_ZONE_SET,
    CONF_ZONENAME,
    CONF_ZONES,
    CONF_ZONETYPE,
    DEFAULT_ZONE_SET,
    DOMAIN,
    LOGGER,
)
from .helpers import generate_range_string, parse_range_string
from .pyenvisalink.honeywell_zone_discovery import FULL_ZONE_SCAN_RANGE, ZoneDiscoveryError


async def async_run_zone_discovery(
    hass: HomeAssistant,
    controller,
    config_entry: ConfigEntry,
    partition_number: int,
    apply: bool = False,
    remove_unused: bool = False,
    include_names: bool = False,
) -> dict[int, dict]:
    """Read zone names/types back from the panel's own installer programming.

    See pyenvisalink/honeywell_zone_discovery.py for exactly what this does
    and doesn't do, and docs/zone_discovery.md for the reasoning. This is a
    READ operation on the panel -- it never modifies zone programming -- but
    it does briefly place the panel into installer programming mode, so it
    refuses to run unless the partition is disarmed, and it is only offered
    for Honeywell/Vista panels.

    Always scans FULL_ZONE_SCAN_RANGE (every valid zone number on a
    Vista-20P/15P: 1-64), not just the zones already configured for this
    integration -- the whole point of discovery is finding zones that
    haven't been added to `zone_set` yet, so there's nothing for the caller
    to aim it at.

    include_names (default False) additionally walks *82 to read each
    zone's alpha descriptor as its name, on top of the *56 zone-type walk
    that always runs. Unlike the *56 walk, this path has NOT been validated
    against real hardware yet -- see
    `honeywell_zone_discovery.HoneywellZoneDiscovery._read_zone_descriptors`.
    Test with apply=False first.

    With apply=False (the default), results are only logged and posted as a
    persistent notification -- nothing about your configured zone
    names/types changes. Pass apply=True to also write the discovered
    types (and names, if include_names was set) into this integration's
    config entry (equivalent to what the original YAML `zones:` config used
    to do), which reloads the integration so the new names take effect.

    With apply=True, a zone that scans back as zone type "00" (Not Used) is
    otherwise left completely alone -- it has no name/type to write, so
    nothing about it changes even if it's already in `zone_set`. Pass
    remove_unused=True (requires apply=True) to additionally drop any zone
    that comes back "00" from `zone_set` (and clear its now-orphaned
    name/type override, if any), so an entity stops being created for it.
    Requires apply=True since remove_unused has nothing to do on a dry run.
    """
    installer_code = config_entry.data.get(CONF_INSTALLER_CODE)
    if not installer_code:
        raise HomeAssistantError(
            "No installer code is configured for this integration. Set one on "
            "the integration's Basic options page (Settings > Devices & "
            "services > this integration > Configure > Basic) before using "
            "zone discovery."
        )
    if remove_unused and not apply:
        raise HomeAssistantError(
            "remove_unused requires apply: true -- there's nothing to remove on a dry run."
        )

    try:
        results = await controller.controller.discover_zone_info(
            installer_code, partition_number, FULL_ZONE_SCAN_RANGE, include_names
        )
    except ZoneDiscoveryError as err:
        LOGGER.error("Zone discovery failed: %s", err)
        raise HomeAssistantError(f"Zone discovery failed: {err}") from err

    summary_lines = [
        f"Zone {zone}: name={info['name']!r}, "
        f"type={info['zone_type_label'] or info['zone_type']} "
        f"(device_class={info['device_class']})"
        for zone, info in sorted(results.items())
    ]
    LOGGER.info("Zone discovery results:\n%s", "\n".join(summary_lines))

    removed_zones: set[int] = set()
    if remove_unused:
        zone_spec = config_entry.data.get(CONF_ZONE_SET, DEFAULT_ZONE_SET)
        configured_zones = set(
            parse_range_string(
                zone_spec, min_val=1, max_val=controller.controller.max_zones
            )
            or []
        )
        removed_zones = configured_zones & {
            zone for zone, info in results.items() if info["zone_type"] == "00"
        }

    await hass.services.async_call(
        "persistent_notification",
        "create",
        {
            "title": "Envisalink zone discovery",
            "notification_id": f"{DOMAIN}_zone_discovery_{config_entry.entry_id}",
            "message": (
                ("Applied to zone names/types below.\n\n" if apply else "")
                + ("Dry run -- nothing was changed. Pass apply: true to save these.\n\n" if not apply else "")
                + (
                    f"Removed unused zone(s) from zone_set: {sorted(removed_zones)}\n\n"
                    if removed_zones
                    else ""
                )
                + "\n".join(summary_lines)
            ),
        },
        blocking=False,
    )

    if apply:
        new_zone_data = dict(config_entry.data.get(CONF_ZONES) or {})
        for zone, info in results.items():
            if not info["name"] and not info["device_class"]:
                continue
            existing = dict(new_zone_data.get(str(zone), {}))
            if info["name"]:
                existing[CONF_ZONENAME] = info["name"]
            if info["device_class"]:
                existing[CONF_ZONETYPE] = info["device_class"]
            new_zone_data[str(zone)] = existing

        new_data = dict(config_entry.data)

        if removed_zones:
            for zone in removed_zones:
                new_zone_data.pop(str(zone), None)

            zone_spec = config_entry.data.get(CONF_ZONE_SET, DEFAULT_ZONE_SET)
            configured_zones = set(
                parse_range_string(
                    zone_spec, min_val=1, max_val=controller.controller.max_zones
                )
                or []
            )
            new_data[CONF_ZONE_SET] = (
                generate_range_string(configured_zones - removed_zones) or DEFAULT_ZONE_SET
            )
            LOGGER.info(
                "Zone discovery: removed %d unused zone(s) from zone_set: %s",
                len(removed_zones),
                sorted(removed_zones),
            )

        new_data[CONF_ZONES] = new_zone_data
        hass.config_entries.async_update_entry(config_entry, data=new_data)
        LOGGER.info(
            "Zone discovery: wrote %d zone(s) into config entry %s",
            len(new_zone_data),
            config_entry.entry_id,
        )

    return results
