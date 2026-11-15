"""MCP tools for Hyundai Bluelink and Kia Connect (and Genesis Connect, via the same library)."""

# No `from __future__ import annotations`: unlock_vehicle's resolver parameter refers
# to a function defined inside build_server, which string annotations couldn't see.

import datetime as dt
from functools import partial
from typing import Annotated, Any, Literal

import anyio.to_thread
from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions, WindowRequestOptions
from hyundai_kia_connect_api.const import WINDOW_STATE
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

from . import __version__, schedule
from .audit import AuditLog, audited
from .client import BluelinkClient, BluelinkError, require
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
    Field(
        description="Seat heating or ventilation, on cars that have it. Leave out to not ask for a "
        "change. In the USA and Canada, Bluelink turns a seat that is left out off."
    ),
]
Day = Literal["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
TimeArg = Annotated[
    str | None,
    Field(
        description="24-hour local time, HH:MM. Leave out to keep it as is.",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    ),
]
KeepBool = Annotated[bool | None, Field(description="Leave out to keep it as is.")]
# The library's charging current levels.
CHARGING_CURRENT_LEVELS = {100: 1, 90: 2, 60: 3}
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

    # Every tool that acts on the car (waking it or sending a command) is wrapped in `logged`.
    audit_log = AuditLog(config.audit_log) if config.audit_log else None
    logged = partial(audited, audit_log)

    # --- read ---------------------------------------------------------------

    @mcp.tool(title="List vehicles", annotations=READ)
    async def list_vehicles() -> list[dict[str, Any]]:
        """List the vehicles on the account."""
        return await run(client.vehicles, vehicle_summary)

    @mcp.tool(title="Get vehicle status", annotations=READ)
    async def get_vehicle_status(vehicle: VehicleArg = None) -> dict[str, Any]:
        """Get the car's last reported state: battery and fuel, range, charging, climate,
        locks, doors, windows, tyres, warnings and charging schedule. Does not wake the car."""
        return await run(client.status, vehicle, vehicle_status)

    @mcp.tool(title="Refresh vehicle status from the car", annotations=WAKE)
    @logged()
    async def refresh_vehicle_status(vehicle: VehicleArg = None) -> dict[str, Any]:
        """Wake the car and fetch live state, then return it like get_vehicle_status.
        Uses a little 12V battery and API quota, so it is rate limited."""
        return await run(client.force_refresh, vehicle, vehicle_status)

    if config.allow_location:

        @mcp.tool(title="Get vehicle location", annotations=READ)
        async def get_vehicle_location(vehicle: VehicleArg = None) -> dict[str, Any]:
            """Get where the car is parked (latitude/longitude, and an address if geocoding is on)."""
            return await run(
                client.status, vehicle, partial(vehicle_location, precision=config.location_precision)
            )

    @mcp.tool(title="Get trips", annotations=READ)
    async def get_trips(
        vehicle: VehicleArg = None,
        date: Annotated[
            str | None,
            Field(
                description="YYYY-MM-DD for that day's trips, or YYYY-MM for a month summary. "
                "Defaults to today in the car's time zone."
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Get driving history: trips for a day, or a month's summary with trip counts per day.
        Not every region supports this (Europe does)."""
        raw = (date or "").strip()
        period: dt.date | str | None = None
        try:
            if len(raw) == 7:
                period = dt.datetime.strptime(raw, "%Y-%m").strftime("%Y-%m")
            elif raw:
                period = dt.datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            raise BluelinkError("date must be YYYY-MM-DD or YYYY-MM") from None

        def view(day: str, info: Any) -> dict[str, Any]:
            return clean({"date": day, "trips": info}) if info else {"date": day, "trips": None}

        return await run(client.trip_info, vehicle, period, view)

    @mcp.tool(title="Get daily energy usage", annotations=READ)
    async def get_energy_usage(
        vehicle: VehicleArg = None,
        days: Annotated[int, Field(ge=1, le=31, description="How many recent days.")] = 7,
    ) -> list[dict[str, Any]]:
        """Get daily electric energy use (Wh) split into driving, climate, electronics and
        battery care, plus regenerated energy and distance. EVs and PHEVs in some regions."""
        return await run(client.status, vehicle, partial(daily_stats, days=days))

    @mcp.tool(title="Get command status", annotations=READ)
    async def get_command_status(
        action_id: Annotated[
            str,
            Field(
                description="The action_id a command returned.",
                max_length=128,
                pattern=r"^[A-Za-z0-9_.:+/=-]+$",
            ),
        ],
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
    @logged()
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
            try:
                car = await run(client.command_target, vehicle, "unlock_vehicle", exact=True)
            except BaseException as ex:
                # The tool itself never runs, so its audit wrapper can't record the refusal.
                if audit_log is not None:
                    audit_log.record("unlock_vehicle", {"vehicle": vehicle}, error=ex)
                raise
            vin = str(car.VIN or "")
            vin_hint = f" (VIN ending {vin[-VIN_SUFFIX:]})" if len(vin) >= VIN_SUFFIX else ""
            return Elicit(
                f"Unlock {car.name}{vin_hint}? The doors will open for anyone near the car.",
                UnlockConfirmation,
            )

        @mcp.tool(title="Unlock the car", annotations=SENSITIVE)
        @logged(skip=("answer",))
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
    @logged()
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
    @logged()
    async def stop_climate(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Stop cabin pre-conditioning."""
        return await command(vehicle, "stop_climate", lambda m, v: m.stop_climate(v.id), wait)

    @mcp.tool(title="Start charging", annotations=WRITE)
    @logged()
    async def start_charging(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Start charging now (the car must be plugged in). Overrides a charging schedule."""
        return await command(vehicle, "start_charging", lambda m, v: m.start_charge(v.id), wait)

    @mcp.tool(title="Stop charging", annotations=WRITE)
    @logged()
    async def stop_charging(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Stop charging."""
        return await command(vehicle, "stop_charging", lambda m, v: m.stop_charge(v.id), wait)

    @mcp.tool(title="Set charge limits", annotations=WRITE)
    @logged()
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

    @mcp.tool(title="Start battery conditioning", annotations=WRITE)
    @logged()
    async def start_battery_conditioning(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Start warming (or cooling) the high-voltage battery, so it charges faster and the
        car has more range when it's cold. Uses energy from the battery, or from the charger
        when plugged in. EVs in europe-cci only."""

        def start(m: Any, v: Any) -> str | None:
            require(m, "start_battery_conditioning", "Battery conditioning")
            return m.start_battery_conditioning(v.id)

        return await command(vehicle, "start_battery_conditioning", start, wait)

    @mcp.tool(title="Stop battery conditioning", annotations=WRITE)
    @logged()
    async def stop_battery_conditioning(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Stop conditioning the high-voltage battery. EVs in europe-cci only."""

        def stop(m: Any, v: Any) -> str | None:
            require(m, "stop_battery_conditioning", "Battery conditioning")
            return m.stop_battery_conditioning(v.id)

        return await command(vehicle, "stop_battery_conditioning", stop, wait)

    @mcp.tool(title="Open the charge port", annotations=WRITE)
    @logged()
    async def open_charge_port(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Open the charging port door, ready to plug in. EVs and plug-in hybrids with a
        motorised port door, in Europe, Australia, New Zealand, China and India."""

        def open_port(m: Any, v: Any) -> str | None:
            require(m, "charge_port_action", "Opening the charge port")
            return m.open_charge_port(v.id)

        return await command(vehicle, "open_charge_port", open_port, wait)

    @mcp.tool(title="Close the charge port", annotations=WRITE)
    @logged()
    async def close_charge_port(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Close the charging port door. EVs and plug-in hybrids with a motorised port door,
        in Europe, Australia, New Zealand, China and India."""

        def close_port(m: Any, v: Any) -> str | None:
            require(m, "charge_port_action", "Closing the charge port")
            return m.close_charge_port(v.id)

        return await command(vehicle, "close_charge_port", close_port, wait)

    @mcp.tool(title="Set the AC charging current", annotations=WRITE)
    @logged()
    async def set_charging_current(
        percent: Annotated[
            Literal[100, 90, 60],
            Field(description="Maximum AC charging current, as a share of the full current: 100, 90 or 60."),
        ],
        vehicle: VehicleArg = None,
        wait: WaitArg = True,
    ) -> dict[str, Any]:
        """Limit the current the car draws when AC charging, e.g. to go easy on a weak
        household socket or circuit. Recent EVs in Europe, Australia, New Zealand, China and
        India."""
        level = CHARGING_CURRENT_LEVELS[percent]

        def set_current(m: Any, v: Any) -> str | None:
            require(m, "set_charging_current", "Setting the charging current")
            return m.set_charging_current(v.id, level)

        return await command(vehicle, "set_charging_current", set_current, wait)

    @mcp.tool(title="Set the charging schedule", annotations=WRITE)
    @logged()
    async def set_charging_schedule(
        scheduled_charging: Annotated[
            bool | None,
            Field(description="Turn scheduled charging on or off. Leave out to keep it as is."),
        ] = None,
        off_peak_start: TimeArg = None,
        off_peak_end: TimeArg = None,
        off_peak_only: Annotated[
            bool | None,
            Field(
                description="Charge only in the off-peak window (true), or prefer it but charge "
                "outside it when needed (false). Leave out to keep it as is."
            ),
        ] = None,
        vehicle: VehicleArg = None,
        wait: WaitArg = True,
    ) -> dict[str, Any]:
        """Change the car's scheduled (off-peak) charging: on or off, and the off-peak window.
        Settings left out keep their current values, read from the car's last reported
        state; the result shows the whole schedule that was sent. EVs and plug-in hybrids
        in Europe, Australia, New Zealand, China and India."""
        changes = {
            "charging_enabled": scheduled_charging,
            "off_peak_start_time": schedule.parse_time(off_peak_start),
            "off_peak_end_time": schedule.parse_time(off_peak_end),
            "off_peak_charge_only_enabled": off_peak_only,
        }
        if all(value is None for value in changes.values()):
            raise BluelinkError("Nothing to change: give at least one setting.")
        sent: dict[str, Any] = {}

        def build(v: Any, scopes: tuple[str, ...]) -> Any:
            options = schedule.build(v, scopes, charging=changes)
            sent["schedule"] = schedule.describe_charging(options)
            return options

        def send(m: Any, v: Any) -> str | None:
            return schedule.send(m, v, schedule.CHARGE, partial(build, v))

        result = await command(vehicle, "set_charging_schedule", send, wait)
        return {**result, **clean(sent)}

    @mcp.tool(title="Set a departure time", annotations=WRITE)
    @logged()
    async def set_departure_schedule(
        slot: Annotated[
            Literal[1, 2], Field(description="Which of the car's two departure times to change.")
        ],
        enabled: KeepBool = None,
        days: Annotated[
            list[Day] | None,
            Field(min_length=1, description="The days it repeats on. Leave out to keep them as they are."),
        ] = None,
        time: TimeArg = None,
        climate: Annotated[
            bool | None,
            Field(description="Pre-condition the cabin for this departure. Leave out to keep it as is."),
        ] = None,
        temperature: Annotated[
            float | None,
            Field(
                ge=17,
                le=27,
                description="Cabin temperature for the departure, 17-27 °C in 0.5 steps. Always in °C; "
                "converted for cars set to °F. Leave out to keep it as is.",
            ),
        ] = None,
        defrost: Annotated[
            bool | None, Field(description="Defrost the windscreen. Leave out to keep it as is.")
        ] = None,
        vehicle: VehicleArg = None,
        wait: WaitArg = True,
    ) -> dict[str, Any]:
        """Change one of the car's two departure times: when it repeats, and whether to
        pre-condition the cabin (and charge) to be ready by then. Settings left out keep
        their current values, read from the car's last reported state; the result shows the
        departure that was sent. On some cars the climate settings are shared by both
        departures. EVs and plug-in hybrids in Europe, Australia, New Zealand, China and India."""
        changes: dict[str, Any] = {
            "enabled": enabled,
            "days": schedule.day_numbers(days),
            "time": schedule.parse_time(time),
            "climate_enabled": climate,
            "defrost": defrost,
        }
        if temperature is None and all(value is None for value in changes.values()):
            raise BluelinkError("Nothing to change: give at least one setting.")
        sent: dict[str, Any] = {}

        def build(v: Any, scopes: tuple[str, ...]) -> Any:
            fahrenheit = schedule.uses_fahrenheit(v)
            if temperature is not None:
                celsius = round(temperature * 2) / 2
                changes["temperature"] = round(celsius * 9 / 5 + 32) if fahrenheit else celsius
            options = schedule.build(v, scopes, slot=slot, departure=changes)
            sent["departure"] = schedule.describe_departure(options, slot, fahrenheit)
            return options

        def send(m: Any, v: Any) -> str | None:
            return schedule.send(m, v, schedule.CLIMATE, partial(build, v))

        result = await command(vehicle, "set_departure_schedule", send, wait)
        return {**result, **clean(sent)}

    @mcp.tool(title="Close the windows", annotations=WRITE)
    @logged()
    async def close_windows(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Close all four side windows, e.g. when the user left one open and rain is coming.
        There is deliberately no tool to open them. Not every car has remote window control;
        not available in the USA or Canada."""

        def close(m: Any, v: Any) -> str | None:
            require(m, "set_windows_state", "Closing the windows")
            closed = WINDOW_STATE.CLOSED
            options = WindowRequestOptions(
                front_left=closed, front_right=closed, back_left=closed, back_right=closed
            )
            return m.set_windows_state(v.id, options)

        return await command(vehicle, "close_windows", close, wait)

    @mcp.tool(title="Flash hazard lights", annotations=WRITE)
    @logged()
    async def flash_hazard_lights(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Flash the hazard lights briefly, e.g. to find the car in a car park. Not available
        in Canada or for Kia in the USA."""

        def flash(m: Any, v: Any) -> str | None:
            require(m, "start_hazard_lights", "Flashing the hazard lights")
            return m.start_hazard_lights(v.id)

        return await command(vehicle, "flash_hazard_lights", flash, wait)

    @mcp.tool(title="Sound the horn and flash the lights", annotations=WRITE)
    @logged()
    async def sound_horn_and_flash_lights(vehicle: VehicleArg = None, wait: WaitArg = True) -> dict[str, Any]:
        """Sound the horn and flash the hazard lights for a short while, e.g. to find the car.
        It is loud and can disturb people nearby, so only do this when the user asks for the
        horn; to just find the car, prefer flash_hazard_lights. Not available in Canada or
        for Kia in the USA."""

        def honk(m: Any, v: Any) -> str | None:
            require(m, "start_hazard_lights_and_horn", "Sounding the horn")
            return m.start_hazard_lights_and_horn(v.id)

        return await command(vehicle, "sound_horn_and_flash_lights", honk, wait)

    return mcp
