""" NWS Alerts """
import logging
from homeassistant import config_entries
from homeassistant.components.sensor import DOMAIN as DOMAIN_SENSOR
from homeassistant.helpers.discovery import async_load_platform
from .const import (DOMAIN)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [DOMAIN_SENSOR]

async def async_setup(hass, config_entry):
    """Set up this component using YAML."""
    if config_entry.get(DOMAIN) is None:
        # We get here if the integration is set up using config flow
        return True

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data={}
        )
    )

    return True


async def async_setup_entry(hass, config_entry):
    """Load the saved entities."""
    hass.config_entries.async_update_entry(config_entry, options=config_entry.data)
    config_entry.add_update_listener(update_listener)

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    
    return True


async def async_unload_entry(hass, config_entry):
    """Handle removal of an entry."""
    try:
        await hass.config_entries.async_forward_entry_unload(config_entry,
                                                             "sensor")
        _LOGGER.info(
            "Successfully removed sensor from the " + DOMAIN + " integration"
        )
    except ValueError:
        pass
    return True


async def update_listener(hass, entry):
    """Update listener."""
    entry.data = entry.options
    await hass.config_entries.async_forward_entry_unload(entry, "sensor")
    hass.async_add_job(hass.config_entries.async_forward_entry_setup(entry,
                                                                     "sensor"))
