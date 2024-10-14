mport aiohttp
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

# COLOR_MAP = {
#     "Dense Fog Advisory": [255, 190, 207],
#     "Winter Weather Advisory": [199, 36, 255],
#     "Winter Storm Warning": [255, 36, 199],
#     "Wind Chill Advisory": [8, 141, 214],
#     "Wind Chill Warning": [176, 196, 222],
#     "Heat Advisory": [255, 99, 27],
#     "Tornado Warning": [255, 0, 0],
#     "Tornado Watch": [255, 190, 0],
#     "Severe Thunderstorm Warning": [255, 160, 0],
#     "Severe Thunderstorm Watch": [255, 149, 0],
#     "Freeze Warning": [199, 36, 255],
# }

SEVERITY_MODIFIERS = {
    "Flood Watch": -2,
    "Flood Warning": -2,
    "Severe Thunderstorm Watch": -1
}

SUB_SEVERITY_MAP = {
    "Severe Thunderstorm Watch": 1,
    "Flood Warning": -1,
    "Flood Watch": -1
}

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_ZONE_ID): cv.string,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
})

HASS_LATITUDE = None
HASS_LONGITUDE = None

async def async_setup_platform(
    hass, config, async_add_entities, discovery_info=None
):
    """ Configuration from yaml """
    name = config.get(CONF_NAME, DEFAULT_NAME)
    zone_id = config.get(CONF_ZONE_ID)
    location = (hass.config.latitude, hass.config.longitude)
    async_add_entities([NWSAlertSensor(name, zone_id, location)], True)


async def async_setup_entry(hass, entry, async_add_entities):
    """ Setup the sensor platform. """
    name = entry.data[CONF_NAME]
    zone_id = entry.data[CONF_ZONE_ID]
    location = (hass.config.latitude, hass.config.longitude)
    async_add_entities([NWSAlertSensor(name, zone_id, location)], True)


class NWSAlertSensor(Entity):
    """Representation of a Sensor."""

    def __init__(self, name, zone_id, location):
        """Initialize the sensor."""
        self._name = name
        self._icon = DEFAULT_ICON
        self._state = "None"
        self._alert_count = 0
        self._alert_active = False
        self._alerts = {}
        self._ends = None
        self._severity = None
        self._zone_id = zone_id.replace(' ', '')
        self._location = location

        _LOGGER.info("NWS home coords: %s", self._location)

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
    def extra_state_attributes(self):
        """Return the state message."""
        attrs = {}

        attrs[ATTR_ATTRIBUTION] = ATTRIBUTION
        attrs["severity"] = self._severity
        attrs["ends"] = self._ends
        attrs["alert_count"] = self._alert_count
        attrs["alerts"] = self._alerts
        attrs["alert_active"] = self._alert_active

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
                _LOGGER.debug("getting alert for %s from %s" %
                              (self._zone_id, url))
                if r.status == 200:
                    data = await r.json()
                else:
                    _LOGGER.error("Received %d from API %s for zone %s" %
                                  (r.status, url, self._zone_id))

        alerts = {}
        high_severity = "None"
        high_severity_value = -1
        prominent_alert = "None"
        prominent_ends = None
        alert_active = False

        if data is not None:
            for feature in data["features"]:

                # _LOGGER.debug(feature)

                alert_type = feature["properties"]["event"]
                sent_date = datetime.fromisoformat(
                    feature["properties"]["sent"])
                effective_date = datetime.fromisoformat(
                    feature["properties"]["effective"])
                expiration_date = datetime.fromisoformat(
                    feature["properties"]["expires"])
                onset = datetime.fromisoformat(
                    feature["properties"]["onset"])
                ends = feature["properties"]["ends"] and datetime.fromisoformat(
                    feature["properties"]["ends"]) or expiration_date #datetime.fromisoformat("2099-12-31T23:59:59-00:00")

                if effective_date < datetime.now(timezone.utc) and expiration_date > datetime.now(timezone.utc):
                    if alert_type not in alerts or onset < alerts[alert_type]["onset"] or (onset == alerts[alert_type]["onset"] and ends < alerts[alert_type]["ends"]):

                        coordinates = None
                        geometry = feature.get("geometry")
                        if geometry:
                            coordinates = geometry.get("coordinates")

                        if coordinates is None or is_point_in_any_polygon(self._location, coordinates):
                            severity = feature["properties"]["severity"]
                            severity_value = (onset < datetime.now(timezone.utc) and ends > datetime.now(
                                timezone.utc) and severity.lower() in SEVERITY_MAP) and SEVERITY_MAP[severity.lower()] or 0

                            if severity_value != 0 and alert_type in SEVERITY_MODIFIERS:
                                severity_value += SEVERITY_MODIFIERS[alert_type]

                            if severity_value > high_severity_value:
                                high_severity_value = severity_value
                                high_severity = severity
                                prominent_alert = alert_type
                                prominent_ends = ends
                            elif severity_value == high_severity_value:
                                sub_severity = (
                                    alert_type in SUB_SEVERITY_MAP and SUB_SEVERITY_MAP[alert_type] or 0)
                                sub_high_severity = (
                                    prominent_alert in SUB_SEVERITY_MAP and SUB_SEVERITY_MAP[prominent_alert] or 0)
                                if sub_severity > sub_high_severity:
                                    high_severity_value = severity_value
                                    high_severity = severity
                                    prominent_alert = alert_type
                                    prominent_ends = ends

                            alerts[alert_type] = {
                                "id": feature["properties"]["id"],
                                "type": feature["properties"]["@type"],
                                "areas": feature["properties"]["areaDesc"].split(";"),
                                "messageType": feature["properties"]["messageType"],
                                "severity": severity,
                                "event": alert_type,
                                "sent": sent_date,
                                "effective": effective_date,
                                "onset": onset,
                                "ends": ends,
                                "active": onset < datetime.now(timezone.utc) and ends > datetime.now(timezone.utc),
                                "expires": expiration_date,
                            }

                            if alerts[alert_type]["active"]:
                                alert_active = True

            if alert_active:
                self._state = prominent_alert
                self._ends = prominent_ends
                self._severity = high_severity
            else:
                self._state = "None"
                self._ends = None
                self._severity = "None"

            self._alert_count = len(alerts)
            self._alerts = alerts
            self._alert_active = alert_active

def is_point_in_any_polygon(point, polygons):
    for p in polygons:
        if is_point_in_polygon(point, p):
            return True
        
    return False

def is_point_in_polygon(point, polygon):
    x, y = point
    n = len(polygon)
    inside = False

    p1y, p1x = polygon[0]
    for i in range(n + 1):
        p2y, p2x = polygon[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y

    return inside