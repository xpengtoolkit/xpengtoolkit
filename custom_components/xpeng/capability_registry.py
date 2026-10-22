"""Apply proven unsupported feature gates while retaining registry identity."""

from __future__ import annotations

from .vehicle_capabilities import ENTITY_FEATURES, from_coordinator


def sync_capability_entities(hass, entry, coordinator) -> None:
    """Disable unsupported entities; only re-enable ones disabled by us.

    A missing response does not change any registry entry. User/config-entry
    disabling always wins. A supported optional entity is enabled even if older
    integration versions registered it disabled by default.
    """
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    prefix = f"xpeng_{coordinator.vin}_"
    caps = from_coordinator(coordinator)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.platform != "xpeng" or not entity.unique_id.startswith(prefix):
            continue
        feature = ENTITY_FEATURES.get(entity.unique_id[len(prefix) :])
        supported = caps.support(feature) if feature else None
        if supported is False and entity.disabled_by is None:
            registry.async_update_entity(
                entity.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
            )
        elif (
            supported is True
            and entity.disabled_by == er.RegistryEntryDisabler.INTEGRATION
        ):
            registry.async_update_entity(entity.entity_id, disabled_by=None)
