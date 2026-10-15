"""MCP tools for Hyundai Bluelink (and Kia Connect / Genesis, via the same library)."""

from __future__ import annotations

import datetime as dt
from functools import partial
from typing import Annotated, Any, Literal

import anyio.to_thread
from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__
from .client import BluelinkClient, BluelinkError
from .config import Config
from .format import clean, daily_stats, vehicle_location, vehicle_status, vehicle_summary

INSTRUCTIONS = """\
Tools for a car connected to Hyundai Bluelink.

get_vehicle_status returns what the car last reported to Bluelink, which is cheap and
does not disturb the car; check last_updated_at. Use refresh_vehicle_status only when
the user needs live data: it wakes the car, drains a little of the 12V battery and
counts against a daily API limit.

Remote commands go to the car over the mobile network and usually take 20-60 seconds.
A status of "timeout" means the car did not answer, not that the command failed.
The vehicle argument is optional when the account has a single car.
"""

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WAKE = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
SENSITIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)

VehicleArg = Annotated[
    str | None,
    Field(description="Vehicle name, VIN or ID. Optional if the account has one vehicle."),
]
WaitArg = Annotated[
    bool,
    Field(description="Wait (up to ~90 s) for the car to confirm the command."),
]

SeatLevel = Literal["off", "low_heat", "medium_heat", "high_heat", "low_cool", "medium_cool", "high_cool"]
SeatArg = Annotated[
    SeatLevel | None,
    Field(description="Seat heating or ventilation, on cars that have it. Leave out to keep it as is."),
]
SEAT_CODES = {
    "off": 0,
    "low_cool": 3,
    "medium_cool": 4,
    "high_cool": 5,
    "low_heat": 6,
    "medium_heat": 7,
    "high_heat": 8,
}


def climate_options(
    temperature: float,
    duration_minutes: int,
    defrost: bool,
    heated_rear_window_and_mirrors: bool,
    heated_steering_wheel: bool,
    seats: dict[str, SeatLevel | None],
) -> ClimateRequestOptions:
    set_temp = round(temperature * 2) / 2
    if not 14 <= set_temp <= 30:
        raise BluelinkError("temperature must be between 14 and 30 °C")
    # Bluelink's "heating" code: 1 both, 2 rear window/mirrors only, 3 steering wheel only.
    heating = {
        (False, False): 0,
        (True, True): 1,
        (True, False): 2,
        (False, True): 3,
    }[(heated_rear_window_and_mirrors, heated_steering_wheel)]
    seat_codes = {k: SEAT_CODES[v] if v else None for k, v in seats.items()}
    return ClimateRequestOptions(
        set_temp=set_temp,
        duration=duration_minutes,
        defrost=defrost,
        climate=True,
        heating=heating,
        steering_wheel=1 if heated_steering_wheel else 0,
        **seat_codes,
    )


def build_server(config: Config, client: BluelinkClient | None = None) -> MCPServer:
    client = client or BluelinkClient(config)
    mcp = MCPServer(
        name="bluelink-mcp",
        title="Hyundai Bluelink",
        instructions=INSTRUCTIONS,
        version=__version__,
    )

    async def run(fn, *args, **kwargs):
        return await anyio.to_thread.run_sync(partial(fn, *args, **kwargs))

    # --- read ---------------------------------------------------------------

    @mcp.tool(title="List vehicles", annotations=READ)
    async def list_vehicles() -> list[dict[str, Any]]:
        """List the vehicles on the Bluelink account."""
        return [vehicle_summary(v) for v in await run(client.vehicles)]

    @mcp.tool(title="Get vehicle status", annotations=READ)
    async def get_vehicle_status(vehicle: VehicleArg = None) -> dict[str, Any]:
        """Get the car's last reported state: battery and fuel, range, charging, climate,
        locks, doors, windows, tyres, warnings and charging schedule. Does not wake the car."""
        return vehicle_status(await run(client.status, vehicle))

    @mcp.tool(title="Refresh vehicle status from the car", annotations=WAKE)
    async def refresh_vehicle_status(vehicle: VehicleArg = None) -> dict[str, Any]:
        """Wake the car and fetch live state, then return it like get_vehicle_status.
        Uses a little 12V battery and API quota, so it is rate limited."""
        return vehicle_status(await run(client.force_refresh, vehicle))

    @mcp.tool(title="Get vehicle location", annotations=READ)
    async def get_vehicle_location(vehicle: VehicleArg = None) -> dict[str, Any]:
        """Get where the car is parked (latitude/longitude, and an address if geocoding is on)."""
        return vehicle_location(await run(client.status, vehicle))

    @mcp.tool(title="Get trips", annotations=READ)
    async def get_trips(
        vehicle: VehicleArg = None,
        date: Annotated[
            str | None,
            Field(
                description="YYYY-MM-DD for that day's trips, or YYYY-MM for a month summary. "
                "Defaults to today."
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Get driving history: trips for a day, or a month's summary with trip counts per day.
        Not every region supports this (Europe does)."""
        raw = (date or dt.date.today().isoformat()).strip()
        try:
            if len(raw) == 7:
                period = dt.datetime.strptime(raw, "%Y-%m").strftime("%Y%m")
            else:
                period = dt.datetime.strptime(raw, "%Y-%m-%d").strftime("%Y%m%d")
        except ValueError:
            raise BluelinkError("date must be YYYY-MM-DD or YYYY-MM") from None
        info = await run(client.trip_info, vehicle, period)
        return clean({"date": raw, "trips": info}) if info else {"date": raw, "trips": None}

    @mcp.tool(title="Get daily energy usage", annotations=READ)
    async def get_energy_usage(
        vehicle: VehicleArg = None,
        days: Annotated[int, Field(ge=1, le=31, description="How many recent days.")] = 7,
    ) -> list[dict[str, Any]]:
        """Get daily electric energy use (Wh) split into driving, climate, electronics and
        battery care, plus regenerated energy and distance. EVs and PHEVs in some regions."""
        return daily_stats(await run(client.status, vehicle), days)

    @mcp.tool(title="Get command status", annotations=READ)
    async def get_command_status(
        action_id: Annotated[str, Field(description="The action_id a command returned.")],
        vehicle: VehicleArg = None,
    ) -> dict[str, str]:
        """Check whether an earlier remote command succeeded (pending, success, failed, timeout)."""
        return {"action_id": action_id, "status": await run(client.action_status, vehicle, action_id)}

    if not config.allow_writes:
        return mcp

    # --- commands -----------------------------------------------------------

    async def command(vehicle, action, wait):
        return await run(client.command, vehicle, action, wait)

    @mcp.tool(title="Lock the car", annotations=WRITE)
    async def lock_vehicle(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Lock the car's doors."""
        return await command(vehicle, lambda m, vid: m.lock(vid), wait)

    if config.allow_unlock:

        @mcp.tool(title="Unlock the car", annotations=SENSITIVE)
        async def unlock_vehicle(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
            """Unlock the car's doors. Only do this when the user clearly asks for it."""
            return await command(vehicle, lambda m, vid: m.unlock(vid), wait)

    @mcp.tool(title="Start climate control", annotations=WRITE)
    async def start_climate(
        vehicle: VehicleArg = None,
        temperature: Annotated[
            float, Field(description="Target cabin temperature, 14-30 °C, in 0.5 steps.")
        ] = 21,
        duration_minutes: Annotated[int, Field(ge=1, le=30, description="How long to run.")] = 10,
        defrost: Annotated[bool, Field(description="Front windscreen defrost.")] = False,
        heated_rear_window_and_mirrors: Annotated[
            bool, Field(description="Heat the rear window and door mirrors.")
        ] = False,
        heated_steering_wheel: Annotated[bool, Field(description="Heat the steering wheel.")] = False,
        front_left_seat: SeatArg = None,
        front_right_seat: SeatArg = None,
        rear_left_seat: SeatArg = None,
        rear_right_seat: SeatArg = None,
        wait: WaitArg = True,
    ) -> dict[str, Any]:
        """Start pre-conditioning the cabin (heating or cooling). Seat settings only apply
        to cars that support them. On a plug-in hybrid this runs from the high-voltage battery."""
        options = climate_options(
            temperature,
            duration_minutes,
            defrost,
            heated_rear_window_and_mirrors,
            heated_steering_wheel,
            {
                "front_left_seat": front_left_seat,
                "front_right_seat": front_right_seat,
                "rear_left_seat": rear_left_seat,
                "rear_right_seat": rear_right_seat,
            },
        )
        return await command(vehicle, lambda m, vid: m.start_climate(vid, options), wait)

    @mcp.tool(title="Stop climate control", annotations=WRITE)
    async def stop_climate(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Stop cabin pre-conditioning."""
        return await command(vehicle, lambda m, vid: m.stop_climate(vid), wait)

    @mcp.tool(title="Start charging", annotations=WRITE)
    async def start_charging(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Start charging now (the car must be plugged in). Overrides a charging schedule."""
        return await command(vehicle, lambda m, vid: m.start_charge(vid), wait)

    @mcp.tool(title="Stop charging", annotations=WRITE)
    async def stop_charging(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Stop charging."""
        return await command(vehicle, lambda m, vid: m.stop_charge(vid), wait)

    @mcp.tool(title="Set charge limits", annotations=WRITE)
    async def set_charge_limits(
        ac_percent: Annotated[
            int, Field(ge=50, le=100, multiple_of=10, description="Limit for AC (home/public) charging.")
        ],
        dc_percent: Annotated[
            int, Field(ge=50, le=100, multiple_of=10, description="Limit for DC rapid charging.")
        ],
        vehicle: VehicleArg = None,
        wait: WaitArg = True,
    ) -> dict[str, Any]:
        """Set the maximum charge level. Mainly for EVs; many plug-in hybrids don't support it."""
        return await command(vehicle, lambda m, vid: m.set_charge_limits(vid, ac_percent, dc_percent), wait)

    @mcp.tool(title="Flash hazard lights", annotations=WRITE)
    async def flash_hazard_lights(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Flash the hazard lights briefly, e.g. to find the car in a car park."""
        return await command(vehicle, lambda m, vid: m.start_hazard_lights(vid), wait)

    return mcp
