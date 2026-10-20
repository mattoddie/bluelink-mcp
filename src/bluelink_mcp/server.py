"""MCP tools for Hyundai Bluelink and Kia Connect (and Genesis Connect, via the same library)."""

# No `from __future__ import annotations`: unlock_vehicle's resolver parameter refers
# to a function defined inside build_server, which string annotations couldn't see.

import datetime as dt
from functools import partial
from typing import Annotated, Any, Literal

import anyio.to_thread
from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions
from mcp.server.mcpserver import (
    AcceptedElicitation,
    Context,
    Elicit,
    ElicitationResult,
    MCPServer,
    Resolve,
)
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__
from .client import BluelinkClient, BluelinkError
from .config import REGIONS, Config
from .format import clean, daily_stats, vehicle_location, vehicle_status, vehicle_summary

INSTRUCTIONS = """\
Tools for a car connected to Hyundai Bluelink, Kia Connect (formerly UVO) or Genesis Connect.

get_vehicle_status returns what the car last reported to the service, which is cheap and
does not disturb the car; check last_updated_at. Use refresh_vehicle_status only when
the user needs live data: it wakes the car, drains a little of the 12V battery and
counts against a daily API limit.

Remote commands go to the car over the mobile network and usually take 20-60 seconds.
A status of "timeout" means the car did not answer, not that the command failed.
The vehicle argument is optional when the account has a single car.
"""

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
# Waking the car uses its 12V battery and API quota, so it isn't a read-only call.
WAKE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
SENSITIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)

VehicleArg = Annotated[
    str | None,
    Field(description="Vehicle name, VIN or ID. Optional if the account has one vehicle."),
]
WaitArg = Annotated[
    bool,
    Field(
        description="Wait (up to ~60 s) for the car to confirm the command. If it hasn't by then, "
        "the status is pending: check later with get_command_status."
    ),
]

SeatLevel = Literal["off", "low_heat", "medium_heat", "high_heat", "low_cool", "medium_cool", "high_cool"]
SeatArg = Annotated[
    SeatLevel | None,
    Field(description="Seat heating or ventilation, on cars that have it. Leave out to keep it as is."),
]
USA_MIN_F, USA_MAX_F = 62, 82
# Bluelink Canada's oldest cars (before model year 2020) start at 16 °C.
CANADA_OLD_MODEL_YEAR, CANADA_OLD_MIN_C = 2020, 16

ConfirmArg = Annotated[
    str | None,
    Field(
        description="Only needed when the MCP client can't ask the user itself (no elicitation "
        "support): the car's name or the last 6 characters of its VIN, exactly as list_vehicles "
        "or get_vehicle_status show them. Ask the user before setting it."
    ),
]


class UnlockConfirmation(BaseModel):
    """What the user answers when the server asks them to confirm an unlock."""

    unlock: bool = Field(title="Unlock the car now", description="Yes to unlock the doors.")


# The protocol revision from which elicitation travels in the tool result instead of as
# a request from the server, so it works over stateless HTTP too.
INPUT_REQUIRED_VERSION = "2026-07-28"
VIN_SUFFIX = 6


def can_elicit(ctx: Context) -> bool:
    """Whether the server can ask the user directly (MCP form elicitation)."""
    try:
        capabilities = ctx.client_capabilities
        version = ctx.protocol_version
        elicitation = capabilities.elicitation if capabilities is not None else None
        if elicitation is None or (elicitation.form is None and elicitation.url is not None):
            return False
        # Older protocol revisions send the question as a request from the server,
        # which needs a channel back to the client.
        # Versions are dates (YYYY-MM-DD), so they compare as strings.
        if version is None or version < INPUT_REQUIRED_VERSION:
            return ctx.session.can_send_request
        return True
    except ValueError:
        # Called outside of an MCP request, as in tests.
        return False


def confirmation_matches(vehicle: Any, confirm: str | None) -> bool:
    """Whether `confirm` is the car's name, or at least the last 6 characters of its VIN."""
    wanted = (confirm or "").strip().lower()
    if not wanted:
        return False
    name = str(vehicle.name or "").strip().lower()
    vin = str(vehicle.VIN or "").strip().lower()
    return wanted == name or (len(wanted) >= VIN_SUFFIX and len(vin) >= VIN_SUFFIX and vin.endswith(wanted))


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
    region: int = REGIONS["europe"],
) -> ClimateRequestOptions:
    set_temp: float = round(temperature * 2) / 2
    if not 14 <= set_temp <= 30:
        raise BluelinkError("temperature must be between 14 and 30 °C")
    if region == REGIONS["usa"]:
        # Bluelink USA takes whole degrees Fahrenheit, from 62 (LO) to 82 (HI).
        set_temp = min(USA_MAX_F, max(USA_MIN_F, round(set_temp * 9 / 5 + 32)))
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
        title="Hyundai Bluelink / Kia Connect",
        instructions=INSTRUCTIONS,
        version=__version__,
    )

    async def run(fn, *args, **kwargs):
        return await anyio.to_thread.run_sync(partial(fn, *args, **kwargs))

    # --- read ---------------------------------------------------------------

    @mcp.tool(title="List vehicles", annotations=READ)
    async def list_vehicles() -> list[dict[str, Any]]:
        """List the vehicles on the account."""
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

    if config.allow_location:

        @mcp.tool(title="Get vehicle location", annotations=READ)
        async def get_vehicle_location(vehicle: VehicleArg = None) -> dict[str, Any]:
            """Get where the car is parked (latitude/longitude, and an address if geocoding is on)."""
            return vehicle_location(await run(client.status, vehicle), config.location_precision)

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

    async def command(vehicle, name, action, wait, exact=False):
        return await run(client.command, vehicle, name, action, wait, exact=exact)

    @mcp.tool(title="Lock the car", annotations=WRITE)
    async def lock_vehicle(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Lock the car's doors."""
        return await command(vehicle, "lock_vehicle", lambda m, v: m.lock(v.id), wait)

    if config.allow_unlock:

        async def ask_to_unlock(ctx: Context, vehicle: str | None) -> Elicit[UnlockConfirmation] | None:
            """Ask the user to confirm the unlock, when the client supports it.

            None means the client can't ask, so the `confirm` argument is checked instead.
            """
            if not can_elicit(ctx):
                return None
            # Also checks the rate limit, so the user isn't asked for an unlock that can't be sent.
            car = await run(client.command_target, vehicle, "unlock_vehicle", exact=True)
            vin = str(car.VIN or "")
            vin_hint = f" (VIN ending {vin[-VIN_SUFFIX:]})" if len(vin) >= VIN_SUFFIX else ""
            return Elicit(
                f"Unlock {car.name}{vin_hint}? The doors will open for anyone near the car.",
                UnlockConfirmation,
            )

        @mcp.tool(title="Unlock the car", annotations=SENSITIVE)
        async def unlock_vehicle(
            # Filled by the server, not the model: the user's answer, or an accepted
            # result without data when the client couldn't be asked.
            answer: Annotated[ElicitationResult[UnlockConfirmation], Resolve(ask_to_unlock)],
            vehicle: VehicleArg = None,
            confirm: ConfirmArg = None,
            wait: WaitArg = True,
        ) -> dict[str, Any]:
            """Unlock the car's doors. Only do this when the user clearly asks for it.
            With several cars on the account, give the car's exact name, VIN or ID.
            The server asks the user to confirm. If the client can't show that question,
            the call fails until `confirm` is set to the car's name or the last 6 characters
            of its VIN: ask the user first, and never guess or reuse an earlier answer."""
            if not isinstance(answer, AcceptedElicitation):
                raise BluelinkError(f"Not unlocked: the user didn't confirm ({answer.action}).")
            if answer.data is not None:
                if not answer.data.unlock:
                    raise BluelinkError("Not unlocked: the user said no.")
            else:
                # The client couldn't ask the user, so the model must repeat the car's identity.
                car = await run(client.command_target, vehicle, "unlock_vehicle", exact=True)
                if not confirmation_matches(car, confirm):
                    raise BluelinkError(
                        "Not unlocked: this needs confirmation. Ask the user to confirm they want to "
                        "unlock the car, then call unlock_vehicle again with confirm set to the car's "
                        f"name or the last {VIN_SUFFIX} characters of its VIN, as list_vehicles shows them."
                    )
            return await command(vehicle, "unlock_vehicle", lambda m, v: m.unlock(v.id), wait, exact=True)

    @mcp.tool(title="Start climate control", annotations=WRITE)
    async def start_climate(
        vehicle: VehicleArg = None,
        temperature: Annotated[
            float,
            Field(
                description="Target cabin temperature, 14-30 °C, in 0.5 steps. Always in °C; "
                "in the USA it is converted to whole °F (62-82)."
            ),
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
            config.region,
        )

        def start(m: Any, v: Any) -> str | None:
            year = v.year
            if (
                config.region == REGIONS["canada"]
                and isinstance(year, int)
                and year < CANADA_OLD_MODEL_YEAR
                and options.set_temp < CANADA_OLD_MIN_C
            ):
                raise BluelinkError(f"This car's lowest temperature is {CANADA_OLD_MIN_C} °C")
            return m.start_climate(v.id, options)

        result = await command(vehicle, "start_climate", start, wait)
        if config.region == REGIONS["usa"]:
            result["temperature_sent"] = f"{options.set_temp} °F"
        return result

    @mcp.tool(title="Stop climate control", annotations=WRITE)
    async def stop_climate(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Stop cabin pre-conditioning."""
        return await command(vehicle, "stop_climate", lambda m, v: m.stop_climate(v.id), wait)

    @mcp.tool(title="Start charging", annotations=WRITE)
    async def start_charging(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Start charging now (the car must be plugged in). Overrides a charging schedule."""
        return await command(vehicle, "start_charging", lambda m, v: m.start_charge(v.id), wait)

    @mcp.tool(title="Stop charging", annotations=WRITE)
    async def stop_charging(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Stop charging."""
        return await command(vehicle, "stop_charging", lambda m, v: m.stop_charge(v.id), wait)

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
        return await command(
            vehicle, "set_charge_limits", lambda m, v: m.set_charge_limits(v.id, ac_percent, dc_percent), wait
        )

    @mcp.tool(title="Flash hazard lights", annotations=WRITE)
    async def flash_hazard_lights(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Flash the hazard lights briefly, e.g. to find the car in a car park."""
        return await command(vehicle, "flash_hazard_lights", lambda m, v: m.start_hazard_lights(v.id), wait)

    return mcp
