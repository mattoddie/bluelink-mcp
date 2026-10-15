"""Turn library objects into compact, JSON-friendly dicts for the assistant.

Fields the car does not report come back as None from the library and are
left out, so a petrol car doesn't get a list of empty EV fields.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from enum import Enum
from typing import Any

from hyundai_kia_connect_api import Vehicle

DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def clean(value: Any) -> Any:
    """Recursively convert to JSON types, dropping None values and empty containers."""
    if isinstance(value, dict):
        out = {k: clean(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v not in (None, {}, [])}
    if isinstance(value, (list, tuple)):
        return [c for c in (clean(v) for v in value) if c not in (None, {}, [])]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return clean({f.name: getattr(value, f.name) for f in dataclasses.fields(value)})
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.name.lower()
    if isinstance(value, float) and value != value:  # NaN
        return None
    return value


def _measure(value: Any, unit: Any) -> Any:
    if value is None:
        return None
    return {"value": value, "unit": unit} if unit else value


def _get(v: Vehicle, name: str) -> Any:
    # Some properties raise when the backing data is missing.
    try:
        return getattr(v, name)
    except Exception:
        return None


def vehicle_summary(v: Vehicle) -> dict[str, Any]:
    return clean(
        {
            "id": v.id,
            "name": v.name,
            "model": v.model,
            "year": v.year,
            "vin": v.VIN,
            "engine_type": v.engine_type,
            "registration_date": v.registration_date,
        }
    )


def vehicle_status(v: Vehicle) -> dict[str, Any]:
    g = lambda name: _get(v, name)  # noqa: E731

    seats = {
        "front_left": v.front_left_seat_status,
        "front_right": v.front_right_seat_status,
        "rear_left": v.rear_left_seat_status,
        "rear_right": v.rear_right_seat_status,
    }
    departures = []
    for n, label in ((1, "first"), (2, "second")):
        enabled = getattr(v, f"ev_{label}_departure_enabled")
        if enabled is None:
            continue
        days = getattr(v, f"ev_{label}_departure_days") or []
        departures.append(
            {
                "slot": n,
                "enabled": enabled,
                "days": [DAYS[d] if isinstance(d, int) and 0 <= d < 7 else d for d in days],
                "time": getattr(v, f"ev_{label}_departure_time"),
                "climate": getattr(v, f"ev_{label}_departure_climate_enabled"),
                "climate_temperature": g(f"ev_{label}_departure_climate_temperature"),
                "defrost": getattr(v, f"ev_{label}_departure_climate_defrost"),
            }
        )

    return clean(
        {
            "vehicle": vehicle_summary(v),
            "last_updated_at": g("last_updated_at"),
            "summary": {
                "locked": v.is_locked,
                "engine_running": v.engine_is_running,
                "odometer": _measure(g("odometer"), g("odometer_unit")),
                "total_range": _measure(g("total_driving_range"), g("total_driving_range_unit")),
                "outside_temperature": g("outside_temperature"),
                "drive_mode": v.drive_mode,
                "valet_mode_active": v.valet_mode_active,
            },
            "battery": {
                "ev_battery_percent": v.ev_battery_percentage,
                "ev_range": _measure(g("ev_driving_range"), g("ev_driving_range_unit")),
                "ev_battery_health_percent": v.ev_battery_soh_percentage,
                "ev_battery_temperature_min": g("ev_battery_temperature_min"),
                "ev_battery_temperature_max": g("ev_battery_temperature_max"),
                "ev_battery_winter_mode": v.ev_battery_winter_mode,
                "battery_heating": v.ev_battery_heating_state,
                "aux_12v_battery_percent": v.car_battery_percentage,
                "aux_12v_battery_warning": v.battery_auxiliary_fail_warning_is_on,
            },
            "fuel": {
                "fuel_level_percent": v.fuel_level,
                "fuel_low": v.fuel_level_is_low,
                "fuel_range": _measure(g("fuel_driving_range"), g("fuel_driving_range_unit")),
            },
            "charging": {
                "plugged_in": v.ev_battery_is_plugged_in,
                "charging": v.ev_battery_is_charging,
                "charging_power_kw": v.ev_charging_power,
                "charge_port_open": v.ev_charge_port_door_is_open,
                "minutes_to_full": g("ev_estimated_current_charge_duration"),
                "minutes_to_full_fast": g("ev_estimated_fast_charge_duration"),
                "minutes_to_full_portable": g("ev_estimated_portable_charge_duration"),
                "minutes_to_full_station": g("ev_estimated_station_charge_duration"),
                "charge_limit_ac_percent": v.ev_charge_limits_ac,
                "charge_limit_dc_percent": v.ev_charge_limits_dc,
                "charging_current_level": v.ev_charging_current,
            },
            "schedule": {
                "scheduled_charging_enabled": v.ev_schedule_charge_enabled,
                "off_peak_start": v.ev_off_peak_start_time,
                "off_peak_end": v.ev_off_peak_end_time,
                "off_peak_charge_only": v.ev_off_peak_charge_only_enabled,
                "departures": departures,
            },
            "climate": {
                "on": v.air_control_is_on,
                "set_temperature": g("air_temperature"),
                "defrost": v.defrost_is_on,
                "steering_wheel_heater": v.steering_wheel_heater_is_on,
                "rear_window_heater": v.back_window_heater_is_on,
                "side_mirror_heater": v.side_mirror_heater_is_on,
                "seats": seats,
            },
            "doors": {
                "front_left_open": v.front_left_door_is_open,
                "front_right_open": v.front_right_door_is_open,
                "rear_left_open": v.back_left_door_is_open,
                "rear_right_open": v.back_right_door_is_open,
                "boot_open": v.trunk_is_open,
                "bonnet_open": v.hood_is_open,
            },
            "windows": {
                "front_left_open": v.front_left_window_is_open,
                "front_right_open": v.front_right_window_is_open,
                "rear_left_open": v.back_left_window_is_open,
                "rear_right_open": v.back_right_window_is_open,
                "sunroof_open": v.sunroof_is_open,
            },
            "tyres": {
                "any_warning": v.tire_pressure_all_warning_is_on,
                "front_left_warning": v.tire_pressure_front_left_warning_is_on,
                "front_right_warning": v.tire_pressure_front_right_warning_is_on,
                "rear_left_warning": v.tire_pressure_rear_left_warning_is_on,
                "rear_right_warning": v.tire_pressure_rear_right_warning_is_on,
                "front_left": v.tire_pressure_front_left,
                "front_right": v.tire_pressure_front_right,
                "rear_left": v.tire_pressure_rear_left,
                "rear_right": v.tire_pressure_rear_right,
                "unit": v.tire_pressure_unit,
            },
            "warnings": {
                "washer_fluid_low": v.washer_fluid_warning_is_on,
                "brake_fluid": v.brake_fluid_warning_is_on,
                "oil_level": v.oil_level_warning_is_on,
                "smart_key_battery_low": v.smart_key_battery_warning_is_on,
                "diagnostic_trouble_codes": v.dtc_count,
                "dtc_descriptions": v.dtc_descriptions,
            },
            "service": {
                "next_service_in": g("next_service_distance"),
                "last_service_at": g("last_service_distance"),
            },
        }
    )


def vehicle_location(v: Vehicle) -> dict[str, Any]:
    lat, lon = v.location_latitude, v.location_longitude
    name, address = _get(v, "geocode") or (None, None)
    return clean(
        {
            "vehicle": v.name,
            "latitude": lat,
            "longitude": lon,
            "updated_at": _get(v, "location_last_updated_at"),
            "place": name,
            "address": address,
            "map": f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=17/{lat}/{lon}"
            if lat is not None and lon is not None
            else None,
        }
    )


def daily_stats(v: Vehicle, days: int) -> list[dict[str, Any]]:
    stats = sorted(_get(v, "daily_stats") or [], key=lambda s: s.date or dt.datetime.min, reverse=True)
    return clean(stats[:days])
