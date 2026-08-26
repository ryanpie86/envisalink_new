# EyezOn Integration for Home Assistant

A modernized version of the Home Assistant `envisalink` integration.

**This is [ryanpie86](https://github.com/ryanpie86)'s fork of [ufodone/envisalink_new](https://github.com/ufodone/envisalink_new)**, with an added Honeywell auto zone-mapping feature (reading zone types back from the panel's own installer programming -- see "Zone name/type discovery" below) on top of everything from the upstream project.

My original intent was to submit these changes back HA core to update the aging `envisalink` integration. However, the scope of the changes got large which meant that the effort to them back into HA core would to be very time consuming. I don't expect to be able to commit the time required to get these changes back into HA core so this integration will unfortunately remain only available through HACS for the foreseeable future.

## Supported devices

- **Envisalink 3/4 for DSC and Honeywell panels**
- **Uno IP Hybrid panels**

## Current changes include:

- Full support for UI configuration flow (configuration.yaml to be deprecated)
- Support for multiple envisalink devices
- Entities now have unique IDs allowing configuration/customization via the UI
- Zone bypass switch support for DSC panels
- Auto-discovery of EVL version and panel type (DSC/HONEYWELL)
- New algorithm on for Honeywell systems to better handle zone open/close status
- Several panel and zone attributes have been promoted to entities
- Support for low battery warnings for wireless sensors on DSC systems. Requires a [specific](https://github.com/ufodone/envisalink_new/issues/63#issuecomment-1888344880) firmware version.
- Refactoring of the underlying pyenvisalink package including
  - Sequential queueing of commands to the EVL including retry on errors (which applicable) and timeouts
  - Ability to query EVL firmware version and MAC address
  - Update of asyncio network handling to use Streams rather than low-level APIs
- Honeywell/**Vista-20P** zone auto-discovery: reads each zone's type (and, in a future release, name) directly from the panel's own installer programming instead of requiring manual entry. Adds a "Panel model" option, a dedicated **Zone Scan** device with a discovery-mode dropdown and a run button, and a `discover_zone_info` service for automations -- see "Zone name/type discovery" below.
- Many other small feature additions and bug fixes.

## Installation

### Install using [HACS](https://hacs.xyz/docs/setup/prerequisites)

You need to add this repository to the custom repository page in HACS before you can install this integration.
To do so first go to the HACS Integrations page. From there click the menu in the top right with the 3 dots.
Use this URL for the repository `https://github.com/ryanpie86/envisalink_new` and select `integration` for the category. After you add the custom repository, just search for `EyezOn` in HACS and install it. Installation will complete after you reboot Home Assistant.

## Configuration

### Config flow

Configuration of the integration has been upgraded to use HA's config flow (via the UI). To add an envisalink device, go to `Settings -> Devices & Services`, click `Add Integration` at the bottom left of the screen and search for `envisalink_new`. This will then prompt you for basic information about the EVL device. Newly added is an `Alarm Name` which is used to prefix the entities created for your EVL.

If you have the installer code for the panel, you can pull in the existing active zones for Vista panels (currently 20P only, non-ADT). A future release will also pull in the existing programmed Alpha Descriptors. It will default to creating no zones and a single partition (1). To adjust the setup to match your system, click the `Configure` button on the newly created device and define the available zones and partitions. The zone and partition list accepts a comma separated list of numerical zones/partitions as well as ranges. For example:

```
1-2,4-8,16-18,20-29
```

Unlike the old configuration.yaml approach, the integration will create its own names for the entities it creates based on the `Alarm Name` setting you provided. These can all be changed using the normal HA method (e.g. find the entity in the UI, click it, go to Settings and make any necessary changes).

### Zone name/type discovery (Honeywell/Vista, experimental)

The number of zones/partitions genuinely can't be discovered automatically, but for Honeywell/Vista panels, each zone's *type* and (if one was ever set) its *name* are already stored in the panel's own installer programming, and can be read back the same way an installer reads them off a physical alpha keypad. Set an installer code and confirm the panel model (currently only "Vista 20P (Non-ADT Panels Only)" is supported) on the integration's Basic options page. Doing so creates a separate **Zone Scan** device (its own card, linked to your alarm panel) with two controls: a **Zone Discovery Mode** dropdown (Preview only / Apply discovered names/types / Apply + remove unused zones) and a **Discover Zone Info** button that runs whichever mode is currently selected. You can also call the `envisalink_new.discover_zone_info` service directly (Developer Tools > Actions) with `apply`/`remove_unused` set explicitly -- handy for automations/scripts. Either way, it always scans every valid zone number for a Vista-20P/15P (1-64) rather than only the zones you've already configured -- the point of discovery is finding zones you haven't set up yet. A zone that comes back type "00" (Not Used) is otherwise left alone even when applying; use the "Apply + remove unused zones" mode (or `remove_unused: true`, which requires `apply: true`) to also drop such zones from your configured `zone_set`. See [docs/zone_discovery.md](docs/zone_discovery.md) for exactly what this does, its safety model, and its current limitations (wireless zones aren't supported yet, and *name* reading is temporarily disabled while the zone-*type* walk is validated against real hardware -- `zone_type` is currently the only field populated). This briefly puts your panel into installer programming mode, so it requires the partition to be disarmed, and it's new -- please read that doc, and start with "Preview only" (the default), before relying on it.

### configuration.yaml

This method of configuring the integration is still available but is meant primarily to allow for easy upgrades for people using the original integration. On startup, the integration will look for the presence of the configuration and import it into a config entity. The intent here is that once setup, all your entity names, etc. If you subsequently change the configuration.yaml, the next HA restart will re-sync your changes into the config entity. However, it is recommended that once the initial import has been done and confirmed working that the entries in configuration.yaml are removed.

Because the name of the integration has been changed (for now) to avoid conflict with the official packaged version, you will need to change your `envisalink` heading to `envisalink_new` so that this HACS integration will pick it up.
