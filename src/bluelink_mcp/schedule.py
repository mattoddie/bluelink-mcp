"""Charging and departure schedules.

Bluelink takes the whole schedule in one request, so a change to one setting has to
send all the others too. The library fills settings left as None with the car's
current values on some backends, but falls back to defaults (midnight, off, 21 °C)
on others, which would quietly overwrite the user's schedule. So the server fills
every setting itself from the car's last reported state, and refuses when the car
doesn't report one that would be sent.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from hyundai_kia_connect_api import Vehicle
from hyundai_kia_connect_api.ApiImpl import ScheduleChargingClimateRequestOptions as Options
from hyundai_kia_connect_api.const import TEMPERATURE_F

from .client import BluelinkError, require
from .format import DAYS

Departure = Options.DepartureOptions

CHARGE = "charge"
CLIMATE = "climate"

# Option field -> (Vehicle attribute, the name the user sees).
CHARGE_FIELDS = {
    "charging_enabled": ("ev_schedule_charge_enabled", "scheduled_charging"),
    "off_peak_start_time": ("ev_off_peak_start_time", "off_peak_start"),
    "off_peak_end_time": ("ev_off_peak_end_time", "off_peak_end"),
    "off_peak_charge_only_enabled": ("ev_off_peak_charge_only_enabled", "off_peak_only"),
}
# Departure option field -> (suffix of ev_{first,second}_departure_<suffix>, the user's name).
DEPARTURE_FIELDS = {
    "enabled": ("enabled", "enabled"),
    "days": ("days", "days"),
    "time": ("time", "time"),
    "climate_enabled": ("climate_enabled", "climate"),
    "temperature": ("climate_temperature", "temperature"),
    "defrost": ("climate_defrost", "defrost"),
}
SLOT_LABELS = {1: "first", 2: "second"}

# europe-cci endpoints: the combined schedule, and the separate charging and climate ones.
COMBINED, CHARGE_ONLY, CLIMATE_ONLY = "reservation-charge-hvac", "reservation-charge", "reservation-hvac"


def parse_time(value: str | None) -> dt.time | None:
    """'HH:MM' (24-hour) to a time. The argument pattern has already checked the format."""
    if value is None:
        return None
    hours, minutes = value.split(":")
    return dt.time(int(hours), int(minutes))


def day_numbers(days: list[str] | None) -> list[int] | None:
    """Day names to Bluelink's numbers (Sun=0 ... Sat=6), in week order without repeats."""
    if days is None:
        return None
    return sorted({DAYS.index(d) for d in days})


def _gspa_allows(api: Any, endpoint: str) -> bool:
    """Whether the library has enabled a europe-cci command for this brand."""
    return bool(getattr(api, "GSPA_REMOTE_CONTROL_VERIFIED", False)) or endpoint in getattr(
        api, "GSPA_VERIFIED_ENDPOINTS", ()
    )


def endpoint(manager: Any, scope: str) -> tuple[str, tuple[str, ...]]:
    """The manager method to call for a change to `scope`, and the scopes it sends.

    The europe-cci backend (GSPA) has a combined endpoint and separate charging and
    climate ones; the library enables them brand by brand. Everywhere else the
    combined call is the only one.
    """
    api = getattr(manager, "api", None)
    if api is not None and hasattr(type(api), "GSPA_VERIFIED_ENDPOINTS") and not _gspa_allows(api, COMBINED):
        if scope == CHARGE and _gspa_allows(api, CHARGE_ONLY):
            return "schedule_reservation_charge", (CHARGE,)
        if scope == CLIMATE and _gspa_allows(api, CLIMATE_ONLY):
            return "schedule_reservation_hvac", (CLIMATE,)
    return "schedule_charging_and_climate", (CHARGE, CLIMATE)


def uses_fahrenheit(v: Vehicle) -> bool:
    return v.ev_first_departure_climate_temperature_unit == TEMPERATURE_F


def build(
    v: Vehicle,
    scopes: tuple[str, ...],
    charging: dict[str, Any] | None = None,
    slot: int | None = None,
    departure: dict[str, Any] | None = None,
) -> Options:
    """Options with every setting in `scopes` filled: the changes, else the car's values.

    `charging` and `departure` hold the changes, keyed by option field (None means
    unchanged). `slot` is the departure (1 or 2) that `departure` changes.
    """
    charging = charging or {}
    departure = departure or {}
    missing: list[str] = []
    options = Options()
    if CHARGE in scopes:
        for field, (attr, name) in CHARGE_FIELDS.items():
            value = charging.get(field)
            if value is None:
                value = getattr(v, attr)
            if value is None:
                missing.append(name)
            setattr(options, field, value)
    if CLIMATE in scopes:
        for n, label in SLOT_LABELS.items():
            dep = Departure()
            changes = departure if n == slot else {}
            for field, (suffix, name) in DEPARTURE_FIELDS.items():
                value = changes.get(field)
                if value is None:
                    value = getattr(v, f"ev_{label}_departure_{suffix}")
                if value is None:
                    missing.append(f"departure {n} {name}")
                setattr(dep, field, value)
            setattr(options, f"{label}_departure", dep)
        # Some backends have one climate setting for both departures: use the one changed.
        main = options.first_departure if slot != 2 else options.second_departure
        options.climate_enabled = main.climate_enabled
        options.temperature = main.temperature
        options.defrost = main.defrost
        options.temperature_unit = 1 if uses_fahrenheit(v) else 0
    if missing:
        raise BluelinkError(
            "Not changed: Bluelink doesn't report this car's current "
            f"{', '.join(missing)}, and the schedule is sent as a whole, so those settings "
            "would be overwritten. Run refresh_vehicle_status and try again, or give them "
            "explicitly if this tool has them as arguments."
        )
    return options


def send(manager: Any, v: Vehicle, scope: str, build_options: Any) -> str | None:
    """Fill and send a schedule change. `build_options(scopes)` makes the options."""
    method, scopes = endpoint(manager, scope)
    require(manager, method, "Changing the schedule")
    # The schedule must be filled from what the car last reported, not from whatever
    # state the server last read.
    manager.update_vehicle_with_cached_state(v.id)
    return getattr(manager, method)(v.id, build_options(scopes))


def describe_charging(options: Options) -> dict[str, Any]:
    return {
        "scheduled_charging": options.charging_enabled,
        "off_peak_start": _hhmm(options.off_peak_start_time),
        "off_peak_end": _hhmm(options.off_peak_end_time),
        "off_peak_only": options.off_peak_charge_only_enabled,
    }


def describe_departure(options: Options, slot: int, fahrenheit: bool) -> dict[str, Any]:
    dep = options.first_departure if slot == 1 else options.second_departure
    assert dep is not None
    return {
        "slot": slot,
        "enabled": dep.enabled,
        "days": [DAYS[d] if isinstance(d, int) and 0 <= d < 7 else d for d in dep.days or []],
        "time": _hhmm(dep.time),
        "climate": dep.climate_enabled,
        "temperature": {"value": dep.temperature, "unit": "°F" if fahrenheit else "°C"},
        "defrost": dep.defrost,
    }


def _hhmm(value: Any) -> Any:
    return value.strftime("%H:%M") if isinstance(value, dt.time) else value
