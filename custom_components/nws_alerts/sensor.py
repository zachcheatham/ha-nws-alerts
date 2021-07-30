import aiohttp
import logging
import voluptuous as vol
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from homeassistant.core import callback
from homeassistant.const import CONF_NAME, ATTR_ATTRIBUTION, EVENT_HOMEASSISTANT_START
from homeassistant.helpers.entity import Entity
from homeassistant.util import Throttle
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.helpers import config_validation as cv
from .const import (
    API_ENDPOINT,
    USER_AGENT,
    DEFAULT_ICON,
    DEFAULT_NAME,
    CONF_ZONE_ID,
    ATTRIBUTION,
)

# ---------------------------------------------------------
# API Documentation
# ---------------------------------------------------------
# https://www.weather.gov/documentation/services-web-api
# https://forecast-v3.weather.gov/documentation
# ---------------------------------------------------------

_LOGGER = logging.getLogger(__name__)

MIN_TIME_BETWEEN_UPDATES = timedelta(minutes=1)
SEVERITY_MAP = {
    "extreme": 4,
    "severe": 3,
    "moderate": 2,
    "minor": 1
}

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_ZONE_ID): cv.string,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
})


async def async_setup_platform(
    hass, config, async_add_entities, discovery_info=None
):
    """ Configuration from yaml """
    name = config.get(CONF_NAME, DEFAULT_NAME)
    zone_id = config.get(CONF_ZONE_ID)
    async_add_entities([NWSAlertSensor(name, zone_id)], True)


async def async_setup_entry(hass, entry, async_add_entities):
    """ Setup the sensor platform. """
    name = entry.data[CONF_NAME]
    zone_id = entry.data[CONF_ZONE_ID]
    async_add_entities([NWSAlertSensor(name, zone_id)], True)


class NWSAlertSensor(Entity):
    """Representation of a Sensor."""

    def __init__(self, name, zone_id):
        """Initialize the sensor."""
        self._name = name
        self._icon = DEFAULT_ICON
        self._state = "None"
        self._alert_count = 0
        self._alerts = {}
        self._severity = "None"
        self._zone_id = zone_id.replace(' ', '')

    @property
    def unique_id(self):
        """
        Return a unique, Home Assistant friendly identifier for this entity.
        """
        return f"{self._name}_{self._name}"

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def icon(self):
        """Return the icon to use in the frontend, if any."""
        return self._icon

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def device_state_attributes(self):
        """Return the state message."""
        attrs = {}

        attrs[ATTR_ATTRIBUTION] = ATTRIBUTION
        attrs["severity"] = self._severity
        attrs["alert_count"] = self._alert_count
        attrs["alerts"] = self._alerts

        return attrs

    async def async_added_to_hass(self):
        """Register callbacks."""
        _LOGGER.debug("Registering: %s...", self.entity_id)

        @callback
        def sensor_startup(event):        
            """Update sensor on startup."""

            self.async_schedule_update_ha_state(True)

        self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_START, sensor_startup
        )        

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    async def async_update(self):

        headers = {"User-Agent": USER_AGENT,
                   "Accept": "application/geo+json"
                   }
        data = None
        url = "%s/alerts/active?zone=%s" % (API_ENDPOINT, self._zone_id)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as r:
                _LOGGER.debug("getting alert for %s from %s" % (self._zone_id, url))
                if r.status == 200:
                    data = await r.json()

        if data is not None:

            alerts = {}
            high_severity = ""
            high_severity_value = 0
            promient_alert = ""

            for feature in data["features"]:

                alert_type = feature["properties"]["event"]
                sent_date = datetime.fromisoformat(feature["properties"]["sent"])
                effective_date = datetime.fromisoformat(
                    feature["properties"]["effective"])
                expiration_date = datetime.fromisoformat(
                    feature["properties"]["expires"])

                if effective_date < datetime.now(timezone.utc) and expiration_date > datetime.now(timezone.utc):
                    if not alert_type in alerts or alerts[alert_type]["effective"] < effective_date:
                        severity = feature["properties"]["severity"]
                        severity_value = severity.lower() in SEVERITY_MAP and SEVERITY_MAP[severity.lower()] or 0

                        _LOGGER.debug(severity_value)

                        if severity_value > high_severity_value:
                            high_severity_value = severity_value
                            high_severity = severity
                            promient_alert = alert_type

                        alerts[alert_type] = {
                            "id": feature["properties"]["id"],
                            "type": feature["properties"]["@type"],
                            "areas": feature["properties"]["areaDesc"].split(";"),
                            "messageType": feature["properties"]["messageType"],
                            "severity": severity,
                            "event": alert_type,
                            "sent": sent_date,
                            "effective": effective_date,
                            "expires": expiration_date,
                        }

            if len(alerts) > 0:
                self._state = promient_alert
                self._severity = high_severity
                self._alert_count = len(alerts)
                self._alerts = alerts
            
                return

        self._state = "None"
        self._severity = "None"
        self._alert_count = 0
        self._alerts = {}
