import datetime as dt
import json
import logging
import os
import stat
import threading

import anyio
import pytest
from hyundai_kia_connect_api import Vehicle
from hyundai_kia_connect_api.ApiImpl import ApiImpl
from hyundai_kia_connect_api.const import ORDER_STATUS
from hyundai_kia_connect_api.exceptions import APIError, AuthenticationError, AuthenticationOTPRequired
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import DailyDrivingStats
from mcp.server.mcpserver.exceptions import ToolError

from bluelink_mcp import __version__
from bluelink_mcp import client as client_module
from bluelink_mcp import server as server_module
from bluelink_mcp.__main__ import (
    MAX_BODY_BYTES,
    _split_host,
    allowed_hosts,
    healthcheck_url,
    http_app,
    main,
    transport_security,
)
from bluelink_mcp.audit import redact_text
from bluelink_mcp.client import BluelinkClient, check_supported
from bluelink_mcp.config import Config, ConfigError, log_level
from bluelink_mcp.server import build_server


class FakeManager:
    """Stands in for VehicleManager; records the calls the server makes."""

    instances: list["FakeManager"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.token = kwargs.get("token")
        self.calls: list[tuple] = []
        car = Vehicle(id="v1", name="Santa Fe", model="SANTA FE PHEV", year=2022, VIN="KMHXYZ")
        car.ev_battery_percentage = 80
        car.is_locked = True
        car.fuel_level = 55.0
        car.location = (51.5, -0.12, dt.datetime(2026, 10, 15, 8, 0))
        self.vehicles = {"v1": car}
        FakeManager.instances.append(self)

    def check_and_refresh_token(self):
        if self.token is None:
            self.calls.append(("login",))
            self.token = Token(
                username=self.kwargs["username"],
                # Like the library, the token carries the password and PIN.
                password=self.kwargs["password"],
                pin=self.kwargs["pin"],
                access_token="abc",
                valid_until=dt.datetime(2030, 1, 1, tzinfo=dt.UTC),
            )
        return False

    def update_vehicle_with_cached_state(self, vid):
        self.calls.append(("cached", vid))

    def force_refresh_vehicle_state(self, vid):
        self.calls.append(("force", vid))

    def lock(self, vid):
        self.calls.append(("lock", vid))
        return "act-1"

    def unlock(self, vid):
        self.calls.append(("unlock", vid))
        return "act-3"

    def start_climate(self, vid, options):
        self.calls.append(("climate", vid, options))
        return "act-2"

    def check_action_status(self, vid, action_id, synchronous=False, timeout=120):
        return ORDER_STATUS.SUCCESS


ENV = {"BLUELINK_USERNAME": "me@example.com", "BLUELINK_PASSWORD": "secret", "BLUELINK_PIN": "1234"}


def make(tmp_path, factory=FakeManager, client_kwargs=None, **extra):
    FakeManager.instances.clear()
    env = {**ENV, "BLUELINK_TOKEN_FILE": str(tmp_path / "tokens.json"), **extra}
    config = Config.from_env(env)
    # Commands poll for confirmation; don't make the tests wait between checks.
    client_kwargs = {"poll_seconds": 0, **(client_kwargs or {})}
    client = BluelinkClient(config, manager_factory=factory, **client_kwargs)
    return build_server(config, client), config


def call(server, name, args=None):
    async def go():
        result = await server.call_tool(name, args or {})
        return result.structured_content, result

    return anyio.run(go)


def tool_names(server):
    return {t.name for t in anyio.run(server.list_tools)}


def test_config_defaults_to_hyundai_europe_read_only(tmp_path):
    config = Config.from_env(ENV)
    assert (config.region, config.brand) == (1, 2)
    assert not config.allow_writes
    assert "secret" not in repr(config)


def test_config_rejects_unlock_without_writes():
    with pytest.raises(ConfigError):
        Config.from_env({**ENV, "BLUELINK_ALLOW_UNLOCK": "true"})


def test_token_cache_defaults_to_xdg_config_home(tmp_path):
    config = Config.from_env({**ENV, "XDG_CONFIG_HOME": str(tmp_path / "cfg"), "HOME": "/nowhere"})
    assert config.token_file == tmp_path / "cfg" / "bluelink-mcp" / "tokens.json"
    config = Config.from_env({**ENV, "HOME": str(tmp_path)})
    assert config.token_file == tmp_path / ".config" / "bluelink-mcp" / "tokens.json"
    assert Config.from_env({**ENV, "BLUELINK_TOKEN_FILE": ""}).token_file is None


def test_read_only_server_has_no_command_tools(tmp_path):
    server, _ = make(tmp_path)
    names = tool_names(server)
    assert "bluelink_get_vehicle_status" in names
    assert not names & {
        "bluelink_lock_vehicle",
        "bluelink_unlock_vehicle",
        "bluelink_start_climate",
        "bluelink_start_charging",
    }


def test_unlock_needs_its_own_switch(tmp_path):
    server, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true")
    assert "bluelink_lock_vehicle" in tool_names(server)
    assert "bluelink_unlock_vehicle" not in tool_names(server)
    server, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true", BLUELINK_ALLOW_UNLOCK="true")
    assert "bluelink_unlock_vehicle" in tool_names(server)


def test_status_and_token_cache(tmp_path):
    server, config = make(tmp_path)
    data, _ = call(server, "bluelink_get_vehicle_status")
    assert data["battery"]["ev_battery_percent"] == 80
    assert data["summary"]["locked"] is True
    # Unknown fields are dropped rather than returned as nulls.
    assert "tyres" not in data

    saved = json.loads(config.token_file.read_text())
    assert saved["access_token"] == "abc"
    assert stat.S_IMODE(os.stat(config.token_file).st_mode) == 0o600
    # The password and PIN are never written to disk.
    assert (saved["password"], saved["pin"]) == (None, None)
    assert "secret" not in config.token_file.read_text()
    assert "1234" not in config.token_file.read_text()

    # A second server reuses the cached token instead of logging in again.
    server2, _ = make(tmp_path)
    call(server2, "bluelink_list_vehicles")
    assert ("login",) not in FakeManager.instances[-1].calls


def test_status_is_cached_briefly(tmp_path):
    server, _ = make(tmp_path)
    call(server, "bluelink_get_vehicle_status")
    call(server, "bluelink_get_energy_usage")
    assert FakeManager.instances[-1].calls.count(("cached", "v1")) == 1


def test_list_vehicles_shape(tmp_path):
    server, _ = make(tmp_path)
    data, _ = call(server, "bluelink_list_vehicles")
    assert data["total"] == data["returned"] == 1
    assert data["items"][0]["name"] == "Santa Fe"


class EnergyManager(FakeManager):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.vehicles["v1"].daily_stats = [
            DailyDrivingStats(date=dt.datetime(2026, 10, d), total_consumed=1000 * d) for d in range(1, 6)
        ]


@pytest.mark.parametrize(("hours", "days"), [(1, 1), (24, 1), (24.5, 2), (72, 3), (744, 5)])
def test_energy_usage_rounds_hours_up_to_days(tmp_path, hours, days):
    server, _ = make(tmp_path, factory=EnergyManager)
    data, _ = call(server, "bluelink_get_energy_usage", {"hours": hours})
    assert (data["total"], data["returned"]) == (5, days)
    assert [i["total_consumed"] for i in data["items"]] == [5000 - 1000 * n for n in range(days)]


def test_energy_usage_defaults_to_a_week(tmp_path):
    server, _ = make(tmp_path, factory=EnergyManager)
    data, _ = call(server, "bluelink_get_energy_usage")
    assert data["returned"] == 5
    with pytest.raises(ToolError):
        call(server, "bluelink_get_energy_usage", {"hours": 0})


def test_force_refresh_is_rate_limited(tmp_path):
    server, _ = make(tmp_path)
    call(server, "bluelink_refresh_vehicle_status")
    with pytest.raises(ToolError, match="12V battery"):
        call(server, "bluelink_refresh_vehicle_status")
    assert FakeManager.instances[-1].calls.count(("force", "v1")) == 1


def test_location(tmp_path):
    server, _ = make(tmp_path, BLUELINK_ALLOW_LOCATION="true")
    data, _ = call(server, "bluelink_get_vehicle_location")
    assert (data["latitude"], data["longitude"]) == (51.5, -0.12)
    assert data["map"] == "https://www.openstreetmap.org/?mlat=51.5&mlon=-0.12#map=17/51.5/-0.12"


def test_location_is_off_by_default(tmp_path):
    server, _ = make(tmp_path)
    assert "bluelink_get_vehicle_location" not in tool_names(server)
    data, _ = call(server, "bluelink_get_vehicle_status")
    assert "51.5" not in json.dumps(data)
    with pytest.raises(ConfigError, match="BLUELINK_GEOCODE needs BLUELINK_ALLOW_LOCATION"):
        Config.from_env({**ENV, "BLUELINK_GEOCODE": "true"})


@pytest.mark.parametrize(
    ("precision", "expected"),
    [(None, (51.5012, -0.1235)), ("2", (51.5, -0.12)), ("0", (52, 0)), ("6", (51.501234, -0.123456))],
)
def test_location_precision(tmp_path, precision, expected):
    class PreciseManager(FakeManager):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.vehicles["v1"].location = (51.50123449, -0.12345649, dt.datetime(2026, 10, 15, 8, 0))

    extra = {"BLUELINK_LOCATION_PRECISION": precision} if precision is not None else {}
    server, _ = make(tmp_path, factory=PreciseManager, BLUELINK_ALLOW_LOCATION="true", **extra)
    data, _ = call(server, "bluelink_get_vehicle_location")
    assert (data["latitude"], data["longitude"]) == expected
    assert f"mlat={expected[0]}&mlon={expected[1]}" in data["map"]


@pytest.mark.parametrize("precision", ["-1", "16", "four"])
def test_location_precision_is_validated(precision):
    with pytest.raises(ConfigError, match="BLUELINK_LOCATION_PRECISION"):
        Config.from_env({**ENV, "BLUELINK_LOCATION_PRECISION": precision})


def test_lock_waits_for_confirmation(tmp_path):
    server, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true")
    data, _ = call(server, "bluelink_lock_vehicle", {"vehicle": "santa"})
    assert data == {"vehicle": "Santa Fe", "action_id": "act-1", "status": "success"}


def test_start_climate_options(tmp_path):
    server, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true")
    call(
        server,
        "bluelink_start_climate",
        {"temperature": 21.3, "heated_steering_wheel": True, "front_left_seat": "high_heat"},
    )
    _, _, options = next(c for c in FakeManager.instances[-1].calls if c[0] == "climate")
    assert options.set_temp == 21.5
    assert options.heating == 3
    assert options.steering_wheel == 1
    assert options.front_left_seat == 8
    assert options.front_right_seat is None

    with pytest.raises(ToolError, match="between 14 and 30"):
        call(server, "bluelink_start_climate", {"temperature": 40})


def test_unknown_vehicle_message(tmp_path):
    server, _ = make(tmp_path)
    with pytest.raises(ToolError, match="No vehicle matches"):
        call(server, "bluelink_get_vehicle_status", {"vehicle": "ioniq"})


# --- token cache and login -------------------------------------------------


def write_cache(config, **fields):
    data = {
        "username": "me@example.com",
        "password": "old-password",
        "pin": "0000",
        "access_token": "cached",
        "valid_until": "2030-01-01T00:00:00+00:00",
        **fields,
    }
    config.token_file.write_text(json.dumps(data))


def test_cached_token_gets_the_configured_password_and_pin(tmp_path):
    server, config = make(tmp_path)
    write_cache(config)
    call(server, "bluelink_list_vehicles")
    token = FakeManager.instances[-1].kwargs["token"]
    assert (token.access_token, token.password, token.pin) == ("cached", "secret", "1234")
    # A cache from an older version is rewritten without the password and PIN.
    saved = json.loads(config.token_file.read_text())
    assert (saved["password"], saved["pin"]) == (None, None)
    assert saved["access_token"] == "cached"
    assert "old-password" not in config.token_file.read_text()


class RejectingManager(FakeManager):
    def check_and_refresh_token(self):
        self.calls.append(("login",))
        raise AuthenticationError("bad password, response body: s3cr3t-fragment")


def test_failed_login_discards_cache_and_backs_off(tmp_path):
    server, config = make(tmp_path, factory=RejectingManager)
    write_cache(config)
    with pytest.raises(ToolError, match="rejected the login") as err:
        call(server, "bluelink_list_vehicles")
    assert "s3cr3t-fragment" not in str(err.value)
    assert "AuthenticationError" in str(err.value)
    assert not config.token_file.exists()

    # No new login attempt until the backoff is over.
    with pytest.raises(ToolError, match="waits before trying again"):
        call(server, "bluelink_list_vehicles")
    assert len(FakeManager.instances) == 1


class OTPManager(FakeManager):
    """Like the library: with no token, every check logs in with the password, then asks for an OTP."""

    logins = 0

    def check_and_refresh_token(self):
        if self.token is None:
            OTPManager.logins += 1
            raise AuthenticationOTPRequired("OTP required to refresh token")
        return False


def test_otp_request_backs_off_instead_of_logging_in_on_every_call(tmp_path):
    OTPManager.logins = 0
    server, _ = make(tmp_path, factory=OTPManager)
    with pytest.raises(ToolError, match="one-time passcode"):
        call(server, "bluelink_list_vehicles")
    for _ in range(4):
        with pytest.raises(ToolError, match="waits before trying again"):
            call(server, "bluelink_list_vehicles")
    assert OTPManager.logins == 1


class OfflineManager(FakeManager):
    def check_and_refresh_token(self):
        self.calls.append(("login",))
        raise ConnectionError("connection refused by 10.0.0.1")


def test_network_errors_during_login_do_not_back_off(tmp_path):
    server, config = make(tmp_path, factory=OfflineManager)
    write_cache(config)
    for _ in range(2):
        with pytest.raises(ToolError, match="Could not reach Bluelink to log in") as err:
            call(server, "bluelink_list_vehicles")
        assert "10.0.0.1" not in str(err.value)
    # Each call tried again, and the cached session was kept.
    assert len(FakeManager.instances) == 2
    assert config.token_file.exists()


def test_other_login_errors_do_not_back_off(tmp_path):
    def broken(**kwargs):
        raise APIError("Unknown brand, raw details")

    server, _ = make(tmp_path, factory=broken)
    for _ in range(2):
        with pytest.raises(ToolError, match="Could not log in.*APIError"):
            call(server, "bluelink_list_vehicles")


def test_login_backoff_can_be_turned_off(tmp_path):
    server, _ = make(tmp_path, factory=RejectingManager, BLUELINK_LOGIN_RETRY_MINUTES="0")
    for _ in range(2):
        with pytest.raises(ToolError, match="rejected the login"):
            call(server, "bluelink_list_vehicles")
    assert len(FakeManager.instances) == 2


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[1, 2]",
        '{"username": "me@example.com"}',
        '{"username": "me@example.com", "access_token": "a", "valid_until": "garbage"}',
    ],
)
def test_bad_token_cache_is_ignored(tmp_path, content):
    server, config = make(tmp_path)
    config.token_file.write_text(content)
    call(server, "bluelink_list_vehicles")
    assert FakeManager.instances[-1].kwargs["token"] is None
    assert ("login",) in FakeManager.instances[-1].calls


def test_manager_construction_errors_are_wrapped(tmp_path):
    def broken(**kwargs):
        raise APIError("Unknown brand, raw details")

    server, _ = make(tmp_path, factory=broken)
    with pytest.raises(ToolError, match="Could not log in.*APIError"):
        call(server, "bluelink_list_vehicles")


def test_token_cache_leaves_no_temporary_files(tmp_path):
    server, config = make(tmp_path)
    call(server, "bluelink_list_vehicles")
    assert [p.name for p in tmp_path.iterdir()] == ["tokens.json"]
    assert stat.S_IMODE(os.stat(config.token_file).st_mode) == 0o600


def test_unsupported_region_and_brand_is_a_config_error():
    check_supported(Config.from_env(ENV))
    with pytest.raises(ConfigError, match="hyundai is not supported in BLUELINK_REGION=nz"):
        check_supported(Config.from_env({**ENV, "BLUELINK_REGION": "nz", "BLUELINK_BRAND": "hyundai"}))


# --- errors ----------------------------------------------------------------


class FailingManager(FakeManager):
    def update_vehicle_with_cached_state(self, vid):
        raise APIError("resMsg: internal detail 12345")

    def force_refresh_vehicle_state(self, vid):
        self.calls.append(("force", vid))
        raise APIError("car did not answer")


def test_library_errors_are_not_passed_through(tmp_path):
    server, _ = make(tmp_path, factory=FailingManager)
    with pytest.raises(ToolError) as err:
        call(server, "bluelink_get_vehicle_status")
    assert str(err.value).endswith("Bluelink request failed: Bluelink returned an error. (APIError)")
    assert "12345" not in str(err.value)


def test_failed_force_refresh_is_rate_limited_too(tmp_path):
    server, _ = make(tmp_path, factory=FailingManager)
    with pytest.raises(ToolError, match="APIError"):
        call(server, "bluelink_refresh_vehicle_status")
    with pytest.raises(ToolError, match="12V battery"):
        call(server, "bluelink_refresh_vehicle_status")
    assert FakeManager.instances[-1].calls.count(("force", "v1")) == 1


# --- commands --------------------------------------------------------------


class SlowCarManager(FakeManager):
    """The car confirms the lock only once someone has read the status in the meantime."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.status_read = threading.Event()

    def update_vehicle_with_cached_state(self, vid):
        super().update_vehicle_with_cached_state(vid)
        if ("lock", vid) in self.calls:
            self.status_read.set()

    def check_action_status(self, vid, action_id, synchronous=False, timeout=120):
        assert not synchronous
        return ORDER_STATUS.SUCCESS if self.status_read.is_set() else ORDER_STATUS.PENDING


def test_other_tools_run_while_a_command_waits(tmp_path):
    server, _ = make(
        tmp_path,
        factory=SlowCarManager,
        client_kwargs={"command_wait_seconds": 10, "poll_seconds": 0.01},
        BLUELINK_ALLOW_WRITES="true",
    )
    results = {}

    async def go():
        async def lock():
            results["lock"] = (await server.call_tool("bluelink_lock_vehicle", {})).structured_content

        async with anyio.create_task_group() as tg:
            tg.start_soon(lock)
            while not FakeManager.instances or ("lock", "v1") not in FakeManager.instances[-1].calls:
                await anyio.sleep(0.01)
            with anyio.fail_after(5):
                results["status"] = (
                    await server.call_tool("bluelink_get_vehicle_status", {})
                ).structured_content

    anyio.run(go)
    assert results["status"]["summary"]["locked"] is True
    assert results["lock"]["status"] == "success"


def test_command_returns_pending_when_the_car_is_slow(tmp_path):
    server, _ = make(
        tmp_path,
        factory=SlowCarManager,
        client_kwargs={"command_wait_seconds": 0.05, "poll_seconds": 0.01},
        BLUELINK_ALLOW_WRITES="true",
    )
    data, _ = call(server, "bluelink_lock_vehicle")
    assert data == {"vehicle": "Santa Fe", "action_id": "act-1", "status": "pending"}


def test_start_climate_in_usa_sends_fahrenheit(tmp_path):
    server, _ = make(
        tmp_path, BLUELINK_ALLOW_WRITES="true", BLUELINK_REGION="usa", BLUELINK_MIN_COMMAND_SECONDS="0"
    )
    sent = []
    for celsius in (21, 14, 30):
        data, _ = call(server, "bluelink_start_climate", {"temperature": celsius})
        sent.append(data["temperature_sent"])
    temps = [c[2].set_temp for c in FakeManager.instances[-1].calls if c[0] == "climate"]
    assert temps == [70, 62, 82]
    assert sent == ["70 °F", "62 °F", "82 °F"]


def test_start_climate_in_canada_respects_older_cars(tmp_path):
    server, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true", BLUELINK_REGION="canada")
    call(server, "bluelink_list_vehicles")
    FakeManager.instances[-1].vehicles["v1"].year = 2019
    with pytest.raises(ToolError, match="lowest temperature is 16"):
        call(server, "bluelink_start_climate", {"temperature": 15})
    call(server, "bluelink_start_climate", {"temperature": 16})
    _, _, options = next(c for c in FakeManager.instances[-1].calls if c[0] == "climate")
    assert options.set_temp == 16


# --- vehicles and formatting -----------------------------------------------


class TwoCarManager(FakeManager):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.vehicles["v2"] = Vehicle(id="v2", name="Ioniq 5", model="IONIQ 5", year=2023, VIN="KMHABC")


def test_blank_vehicle_means_the_only_car(tmp_path):
    server, _ = make(tmp_path)
    data, _ = call(server, "bluelink_get_vehicle_status", {"vehicle": "   "})
    assert data["vehicle"]["id"] == "v1"


def test_blank_vehicle_is_ambiguous_with_several_cars(tmp_path):
    server, _ = make(tmp_path, factory=TwoCarManager)
    with pytest.raises(ToolError, match="say which one"):
        call(server, "bluelink_get_vehicle_status", {"vehicle": " "})


def test_unlock_needs_an_exact_vehicle_with_several_cars(tmp_path):
    server, _ = make(
        tmp_path, factory=TwoCarManager, BLUELINK_ALLOW_WRITES="true", BLUELINK_ALLOW_UNLOCK="true"
    )
    # Part of a name is fine for locking...
    data, _ = call(server, "bluelink_lock_vehicle", {"vehicle": "santa"})
    assert data["vehicle"] == "Santa Fe"
    # ...but not for unlocking.
    with pytest.raises(ToolError, match="exact name"):
        call(server, "bluelink_unlock_vehicle", {"vehicle": "santa"})
    assert not any(c[0] == "unlock" for c in FakeManager.instances[-1].calls)
    data, _ = call(server, "bluelink_unlock_vehicle", {"vehicle": "santa fe", "confirm": "Santa Fe"})
    assert (data["vehicle"], data["status"]) == ("Santa Fe", "success")
    data, _ = call(server, "bluelink_unlock_vehicle", {"vehicle": "KMHABC", "confirm": "KMHABC"})
    assert data["vehicle"] == "Ioniq 5"


def test_fuel_range_has_its_unit(tmp_path):
    class PetrolManager(FakeManager):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.vehicles["v1"].fuel_driving_range = (420, "km")

    server, _ = make(tmp_path, factory=PetrolManager)
    data, _ = call(server, "bluelink_get_vehicle_status")
    assert data["fuel"]["fuel_range"] == {"value": 420, "unit": "km"}


# --- HTTP settings ---------------------------------------------------------

TOKEN = "t" * 24


def http_config(**extra):
    return Config.from_env({**ENV, "MCP_TRANSPORT": "http", "MCP_AUTH_TOKEN": TOKEN, **extra})


async def inner_app(scope, receive, send):
    """Stands in for the MCP app: echoes the request body."""
    message = await receive()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"inner:" + message.get("body", b"")})


def request(app, method="POST", path="/mcp", headers=None, body=b"{}", chunks=None):
    """Send one request to an ASGI app; return (status, headers, body)."""
    headers = {"host": "127.0.0.1:8080", **(headers or {})}
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(k.lower().encode(), v.encode("latin-1")) for k, v in headers.items() if v is not None],
    }
    pending = [{"type": "http.request", "body": c, "more_body": True} for c in (chunks or [])]
    pending.append({"type": "http.request", "body": body, "more_body": False})
    out = {"status": None, "headers": {}, "body": b""}

    async def receive():
        return pending.pop(0) if pending else {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            out["status"] = message["status"]
            out["headers"] = {k.decode(): v.decode() for k, v in message.get("headers", [])}
        else:
            out["body"] += message.get("body", b"")

    anyio.run(app, scope, receive, send)
    return out["status"], out["headers"], out["body"]


def guarded(**extra):
    return http_app(http_config(**extra), inner_app)


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Bearer wrong-token-0000000000",
        f"Basic {TOKEN}",
        TOKEN,
        f"Bearer {TOKEN}x",
    ],
)
def test_bearer_auth_rejects_missing_or_wrong_tokens(authorization):
    status, headers, body = request(guarded(), headers={"authorization": authorization})
    assert status == 401
    assert headers["www-authenticate"] == "Bearer"
    assert b"inner" not in body


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER", "bEaReR"])
def test_bearer_auth_accepts_the_token_with_any_case_of_scheme(scheme):
    status, _, body = request(guarded(), headers={"authorization": f"{scheme} {TOKEN}"})
    assert (status, body) == (200, b"inner:{}")


def test_health_needs_no_token_or_allowed_host():
    app = guarded(MCP_ALLOWED_HOSTS="bluelink.example.com")
    status, _, body = request(app, method="GET", path="/health", headers={"host": "evil.example"}, body=b"")
    assert (status, body) == (200, b"inner:")


AUTH = {"authorization": f"Bearer {TOKEN}"}


@pytest.mark.parametrize("method", ["GET", "DELETE", "PUT"])
def test_mcp_is_post_only(method):
    status, headers, _ = request(guarded(), method=method, headers=AUTH)
    assert status == 405
    assert headers["allow"] == "POST"


def test_mcp_needs_a_json_body_within_the_limit():
    assert request(guarded(), headers=AUTH, body=b"{not json")[0] == 400
    assert request(guarded(), headers=AUTH, body=b"x" * (MAX_BODY_BYTES + 1))[0] == 413
    big = b'{"a": "' + b"x" * (MAX_BODY_BYTES - 20) + b'"}'
    assert request(guarded(), headers=AUTH, chunks=[big[:100]], body=big[100:])[0] == 200


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("bluelink.example.com", True),
        ("bluelink.example.com:8443", True),  # an entry without a port allows any port
        ("BLUELINK.example.com", True),
        ("192.168.1.20:8080", True),
        ("192.168.1.20:9090", False),
        ("192.168.1.20", False),
        ("localhost:1234", True),  # localhost:*
        ("[::1]:8080", True),
        ("evil.example", False),
        ("bluelink.example.com.evil.example", False),
        ("", False),
        (None, False),
    ],
)
def test_host_allow_list(host, allowed):
    app = guarded(MCP_ALLOWED_HOSTS="bluelink.example.com, 192.168.1.20:8080, localhost:*, [::1]")
    status, _, _ = request(app, headers={**AUTH, "host": host})
    assert status == (200 if allowed else 421)


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        ("https://bluelink.example.com", True),
        ("http://bluelink.example.com", True),
        ("https://bluelink.example.com:443", True),
        ("https://bluelink.example.com:8443", False),  # other ports must be listed
        ("http://192.168.1.20:8080", True),
        ("http://192.168.1.20", False),
        ("http://localhost:5173", True),  # localhost:*
        ("https://claude.ai", True),  # MCP_ALLOWED_ORIGINS
        ("https://claude.ai:444", False),
        ("http://claude.ai", False),
        ("http://127.0.0.1:6274", True),  # http://127.0.0.1:* in MCP_ALLOWED_ORIGINS
        ("https://evil.example", False),
        ("null", False),
        ("", False),
        ("file://", False),
        ("https://bluelink.example.com/path", False),
    ],
)
def test_origin_allow_list(origin, allowed):
    app = guarded(
        MCP_ALLOWED_HOSTS="bluelink.example.com, 192.168.1.20:8080, localhost:*",
        MCP_ALLOWED_ORIGINS="https://claude.ai, http://127.0.0.1:*",
    )
    status, _, _ = request(app, headers={**AUTH, "host": "bluelink.example.com", "origin": origin})
    assert status == (200 if allowed else 403)


def test_our_host_check_always_replaces_the_sdks():
    assert not transport_security(Config.from_env(ENV)).enable_dns_rebinding_protection
    assert not transport_security(http_config(MCP_ALLOWED_HOSTS="h")).enable_dns_rebinding_protection
    with pytest.raises(ConfigError, match="MCP_ALLOWED_ORIGINS needs MCP_ALLOWED_HOSTS"):
        Config.from_env({**ENV, "MCP_ALLOWED_ORIGINS": "https://claude.ai"})


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("127.0.0.1:8080", True),
        ("localhost:8080", True),
        ("[::1]:8080", True),
        ("evil.example", False),
        ("evil.example:8080", False),
        (None, False),
    ],
)
def test_loopback_binds_allow_only_loopback_hosts_by_default(host, allowed):
    status, _, _ = request(guarded(), headers={**AUTH, "host": host})
    assert status == (200 if allowed else 421)


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "", "http://127.0.0.1:9999"])
def test_origins_are_checked_without_allowed_hosts_too(origin):
    status, _, _ = request(guarded(), headers={**AUTH, "origin": origin})
    assert status == 403


def test_other_binds_without_allowed_hosts_accept_any_host_but_no_browsers():
    config = http_config(MCP_HTTP_HOST="0.0.0.0")
    assert allowed_hosts(config) is None
    app = http_app(config, inner_app)
    assert request(app, headers={**AUTH, "host": "bluelink.lan:8080"})[0] == 200
    for origin in ("http://bluelink.lan:8080", "", "null"):
        assert request(app, headers={**AUTH, "host": "bluelink.lan:8080", "origin": origin})[0] == 403
    # The token is still needed.
    assert request(app, headers={"host": "bluelink.lan:8080"})[0] == 401


@pytest.mark.parametrize("bind", ["127.0.0.1", "127.0.0.2", "localhost", "::1", "[::1]"])
def test_loopback_binds_are_recognised(bind):
    assert allowed_hosts(http_config(MCP_HTTP_HOST=bind)) == ("localhost", "127.0.0.1", "[::1]")


@pytest.mark.parametrize(
    "entry", ["https://claude.ai", "https://claude.ai:443", "https://claude.ai/", "HTTPS://Claude.ai:443/"]
)
def test_allowed_origins_may_give_the_default_port(entry):
    app = guarded(MCP_ALLOWED_HOSTS="bluelink.example.com", MCP_ALLOWED_ORIGINS=entry)
    headers = {**AUTH, "host": "bluelink.example.com"}
    assert request(app, headers={**headers, "origin": "https://claude.ai"})[0] == 200
    assert request(app, headers={**headers, "origin": "https://claude.ai:443"})[0] == 200
    assert request(app, headers={**headers, "origin": "https://claude.ai:8443"})[0] == 403
    assert request(app, headers={**headers, "origin": "http://claude.ai"})[0] == 403


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.com", ("example.com", None)),
        ("Example.com:8080", ("example.com", "8080")),
        ("[::1]", ("[::1]", None)),
        ("[::1]:8080", ("[::1]", "8080")),
        ("[::1]:*", ("[::1]", "*")),
        ("::1", ("[::1]", None)),
        ("fe80::1", ("[fe80::1]", None)),
        ("[::1]junk", ("[::1]junk", None)),
    ],
)
def test_split_host_handles_ipv6(raw, expected):
    assert _split_host(raw) == expected


def test_bare_ipv6_allowed_host_matches_bracketed_host_header():
    app = guarded(MCP_ALLOWED_HOSTS="::1")
    assert request(app, headers={**AUTH, "host": "[::1]:8080"})[0] == 200
    assert request(app, headers={**AUTH, "host": "[::1]junk"})[0] == 421


def test_auth_token_minimum_length():
    with pytest.raises(ConfigError, match="at least 16 characters"):
        http_config(MCP_AUTH_TOKEN="short-token")
    assert http_config(MCP_AUTH_TOKEN="x" * 16).auth_token == "x" * 16


def test_secrets_can_come_from_files(tmp_path):
    (tmp_path / "password").write_text("pass word \n")
    (tmp_path / "pin").write_text("4321\n")
    (tmp_path / "token").write_text(TOKEN + "\n")
    config = Config.from_env(
        {
            "BLUELINK_USERNAME": "me@example.com",
            "BLUELINK_PASSWORD_FILE": str(tmp_path / "password"),
            "BLUELINK_PIN_FILE": str(tmp_path / "pin"),
            "MCP_AUTH_TOKEN_FILE": str(tmp_path / "token"),
        }
    )
    assert (config.password, config.pin, config.auth_token) == ("pass word ", "4321", TOKEN)
    with pytest.raises(ConfigError, match="not both"):
        Config.from_env({**ENV, "BLUELINK_PASSWORD_FILE": str(tmp_path / "password")})
    with pytest.raises(ConfigError, match="Could not read BLUELINK_PIN_FILE"):
        Config.from_env({**ENV, "BLUELINK_PIN": "", "BLUELINK_PIN_FILE": str(tmp_path / "missing")})


@pytest.mark.parametrize(
    ("env", "url"),
    [
        ({}, None),
        ({"MCP_TRANSPORT": "stdio"}, None),
        ({"MCP_TRANSPORT": "http"}, "http://127.0.0.1:8080/health"),
        (
            {"MCP_TRANSPORT": "http", "MCP_HTTP_HOST": "0.0.0.0", "MCP_HTTP_PORT": "9000"},
            "http://127.0.0.1:9000/health",
        ),
        ({"MCP_TRANSPORT": "http", "MCP_HTTP_HOST": "::"}, "http://[::1]:8080/health"),
        ({"MCP_TRANSPORT": "http", "MCP_HTTP_HOST": "10.0.0.5"}, "http://10.0.0.5:8080/health"),
        ({"MCP_TRANSPORT": "http", "MCP_HTTP_HOST": "fd00::5"}, "http://[fd00::5]:8080/health"),
    ],
)
def test_healthcheck_url(env, url):
    assert healthcheck_url(env) == url


@pytest.mark.parametrize("arg", ["version", "--version", "-v"])
def test_version_command(monkeypatch, capsys, arg):
    monkeypatch.setattr("sys.argv", ["bluelink-mcp", arg])
    main()
    assert capsys.readouterr().out.strip() == __version__


@pytest.mark.parametrize(
    ("raw", "level", "library"),
    [
        (None, logging.INFO, logging.CRITICAL + 1),
        ("warning", logging.WARNING, logging.CRITICAL + 1),
        ("ERROR", logging.ERROR, logging.CRITICAL + 1),
        ("debug", logging.DEBUG, logging.DEBUG),
    ],
)
def test_log_level(raw, level, library):
    assert log_level({} if raw is None else {"LOG_LEVEL": raw}) == (level, library)


def test_log_level_is_validated():
    with pytest.raises(ConfigError, match="LOG_LEVEL must be one of"):
        log_level({"LOG_LEVEL": "verbose"})


@pytest.mark.parametrize("minutes", ["0", "-5"])
def test_min_refresh_minutes_must_be_positive(minutes):
    with pytest.raises(ConfigError, match="BLUELINK_MIN_REFRESH_MINUTES must be at least 1"):
        Config.from_env({**ENV, "BLUELINK_MIN_REFRESH_MINUTES": minutes})


def test_refresh_is_not_marked_read_only(tmp_path):
    server, _ = make(tmp_path)
    tools = {t.name: t for t in anyio.run(server.list_tools)}
    assert tools["bluelink_refresh_vehicle_status"].annotations.read_only_hint is False
    assert tools["bluelink_get_vehicle_status"].annotations.read_only_hint is True


# --- MCP Bundle settings ------------------------------------------------------------


def test_unfilled_bundle_settings_are_unset(tmp_path):
    """Claude Desktop passes an optional setting left empty as "" or as the literal placeholder."""
    for blank in ("", "${user_config.%s}"):
        env = {
            **ENV,
            "BLUELINK_PIN": blank.replace("%s", "pin"),
            "BLUELINK_REGION": blank.replace("%s", "region"),
            "BLUELINK_BRAND": blank.replace("%s", "brand"),
            "BLUELINK_ALLOW_WRITES": blank.replace("%s", "allow_writes"),
            "BLUELINK_ALLOW_UNLOCK": blank.replace("%s", "allow_unlock"),
            "BLUELINK_ALLOW_LOCATION": blank.replace("%s", "allow_location"),
            "BLUELINK_AUDIT_LOG": blank.replace("%s", "audit_log"),
            "MCP_TRANSPORT": blank.replace("%s", "transport"),
            "LOG_LEVEL": blank.replace("%s", "log_level"),
        }
        config = Config.from_env(env)
        assert (config.region, config.brand, config.pin) == (1, 2, "")
        assert not (config.allow_writes or config.allow_unlock or config.allow_location)
        assert (config.audit_log, config.transport) == ("", "stdio")
        assert log_level(env)[0] == logging.INFO


def test_placeholder_credentials_are_missing():
    with pytest.raises(ConfigError, match="BLUELINK_USERNAME and BLUELINK_PASSWORD"):
        Config.from_env({**ENV, "BLUELINK_PASSWORD": "${user_config.password}"})


def test_only_a_whole_placeholder_is_unset():
    config = Config.from_env({**ENV, "BLUELINK_PASSWORD": "x${user_config.password}"})
    assert config.password == "x${user_config.password}"


# --- Audit log ----------------------------------------------------------------------


def audit_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_audit_log_records_commands_only(tmp_path):
    log = tmp_path / "audit.jsonl"
    server, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true", BLUELINK_AUDIT_LOG=str(log))
    assert stat.S_IMODE(os.stat(log).st_mode) == 0o600
    assert log.read_text() == ""

    call(server, "bluelink_get_vehicle_status")
    call(server, "bluelink_list_vehicles")
    assert log.read_text() == ""

    call(server, "bluelink_lock_vehicle", {"wait": False})
    call(server, "bluelink_refresh_vehicle_status")
    [lock, refresh] = audit_lines(log)
    assert lock["tool"] == "bluelink_lock_vehicle"
    assert lock["arguments"] == {"vehicle": None, "wait": False}
    assert lock["result"] == {"vehicle": "Santa Fe", "action_id": "act-1", "status": "pending"}
    assert lock["outcome"] == "ok"
    assert lock["time"].endswith("Z")
    # A status result is reduced to the car's name: no VIN, no location, nothing else.
    assert refresh == {
        **refresh,
        "tool": "bluelink_refresh_vehicle_status",
        "result": {"vehicle": "Santa Fe"},
    }
    assert "KMHXYZ" not in log.read_text() and "51.5" not in log.read_text()


def test_audit_log_records_failures(tmp_path):
    log = tmp_path / "audit.jsonl"
    server, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true", BLUELINK_AUDIT_LOG=str(log))
    call(server, "bluelink_lock_vehicle", {"wait": False})
    with pytest.raises(ToolError):
        call(server, "bluelink_lock_vehicle", {"wait": False})
    entry = audit_lines(log)[-1]
    assert entry["outcome"] == "error"
    assert "bluelink_lock_vehicle was sent" in entry["error"]


def test_audit_log_redacts_unlock_confirmation_and_vins(tmp_path):
    log = tmp_path / "audit.jsonl"
    server, _ = make(
        tmp_path,
        BLUELINK_ALLOW_WRITES="true",
        BLUELINK_ALLOW_UNLOCK="true",
        BLUELINK_AUDIT_LOG=str(log),
    )
    with pytest.raises(ToolError):
        call(server, "bluelink_unlock_vehicle", {"vehicle": "KMHXYZ12345678901", "confirm": "wrong"})
    call(server, "bluelink_unlock_vehicle", {"confirm": "Santa Fe", "wait": False})
    refused, unlocked = audit_lines(log)
    assert refused["outcome"] == "error"
    assert refused["arguments"]["vehicle"] == "…678901"
    assert refused["arguments"]["confirm"] == "[redacted]"
    assert "answer" not in refused["arguments"]
    assert unlocked["outcome"] == "ok" and unlocked["result"]["action_id"] == "act-3"
    assert "Santa Fe" not in json.dumps(unlocked["arguments"])


def test_audit_log_to_stderr(tmp_path, capsys):
    server, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true", BLUELINK_AUDIT_LOG="stderr")
    call(server, "bluelink_lock_vehicle", {"wait": False})
    entry = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert entry["tool"] == "bluelink_lock_vehicle"


def test_audit_log_needs_a_writable_directory(tmp_path):
    with pytest.raises(ConfigError, match="BLUELINK_AUDIT_LOG"):
        Config.from_env({**ENV, "BLUELINK_AUDIT_LOG": str(tmp_path / "missing" / "audit.jsonl")})


def test_audit_log_keeps_tool_schemas(tmp_path):
    plain, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true", BLUELINK_ALLOW_UNLOCK="true")
    logged, _ = make(
        tmp_path,
        BLUELINK_ALLOW_WRITES="true",
        BLUELINK_ALLOW_UNLOCK="true",
        BLUELINK_AUDIT_LOG=str(tmp_path / "audit.jsonl"),
    )
    schemas = lambda s: {t.name: t.input_schema for t in anyio.run(s.list_tools)}  # noqa: E731
    assert schemas(plain) == schemas(logged)


# --- review fixes ------------------------------------------------------------


class ReconnectingManager(FakeManager):
    """A fresh manager knows the car but not its state until the cache is read."""

    fail_next = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.vehicles["v1"].is_locked = None
        self.vehicles["v1"].ev_battery_percentage = None

    def check_and_refresh_token(self):
        self.calls.append(("check",))
        if ReconnectingManager.fail_next:
            ReconnectingManager.fail_next = False
            raise ConnectionError("blip")
        return super().check_and_refresh_token()

    def update_vehicle_with_cached_state(self, vid):
        super().update_vehicle_with_cached_state(vid)
        self.vehicles[vid].is_locked = True
        self.vehicles[vid].ev_battery_percentage = 80


def test_status_after_a_reconnect_reads_the_cache_again(tmp_path, monkeypatch):
    server, _ = make(tmp_path, factory=ReconnectingManager)
    first, _ = call(server, "bluelink_get_vehicle_status")
    assert first["battery"]["ev_battery_percent"] == 80
    # Force the next call to check the session, and make that check fail.
    monkeypatch.setattr(client_module, "TOKEN_CHECK_SECONDS", 0)
    ReconnectingManager.fail_next = True
    with pytest.raises(ToolError):
        call(server, "bluelink_get_vehicle_status")
    again, _ = call(server, "bluelink_get_vehicle_status")
    assert len(FakeManager.instances) == 2
    assert again["summary"]["locked"] is True
    assert again["battery"]["ev_battery_percent"] == 80


def test_session_is_not_checked_on_every_call(tmp_path, monkeypatch):
    server, _ = make(tmp_path, factory=ReconnectingManager, BLUELINK_ALLOW_WRITES="true")

    statuses = iter([ORDER_STATUS.PENDING, ORDER_STATUS.PENDING, ORDER_STATUS.SUCCESS])
    monkeypatch.setattr(ReconnectingManager, "check_action_status", lambda *a, **k: next(statuses))
    call(server, "bluelink_get_vehicle_status")
    call(server, "bluelink_list_vehicles")
    data, _ = call(server, "bluelink_lock_vehicle")
    assert data["status"] == "success"
    manager = FakeManager.instances[-1]
    assert manager.calls.count(("check",)) == 1

    # A failed request means the session gets checked before the next one.
    monkeypatch.setattr(ReconnectingManager, "force_refresh_vehicle_state", lambda *a: 1 / 0)
    with pytest.raises(ToolError):
        call(server, "bluelink_refresh_vehicle_status")
    call(server, "bluelink_list_vehicles")
    assert manager.calls.count(("check",)) == 2

    # And again once the check interval has passed.
    monkeypatch.setattr(client_module, "TOKEN_CHECK_SECONDS", 0)
    call(server, "bluelink_list_vehicles")
    assert manager.calls.count(("check",)) == 3


def test_session_is_checked_when_the_token_is_about_to_expire(tmp_path):
    class ExpiringManager(ReconnectingManager):
        def check_and_refresh_token(self):
            super().check_and_refresh_token()
            self.token.valid_until = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=30)

    server, _ = make(tmp_path, factory=ExpiringManager)
    call(server, "bluelink_list_vehicles")
    call(server, "bluelink_list_vehicles")
    assert FakeManager.instances[-1].calls.count(("check",)) == 2


def test_status_values_have_units(tmp_path):
    class USManager(FakeManager):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            car = self.vehicles["v1"]
            car.outside_temperature = (68, "°F")
            car.air_temperature = (72, "°F")
            car.ev_battery_temperature_min = (59, "°F")
            car.ev_battery_temperature_max = (64, "°F")
            car.ev_estimated_current_charge_duration = (95, "m")
            car.next_service_distance = (4200, "mi")
            car.last_service_distance = (11000, "mi")
            car.ev_first_departure_enabled = True
            car.ev_first_departure_climate_temperature = (70, "°F")

    server, _ = make(tmp_path, factory=USManager)
    data, _ = call(server, "bluelink_get_vehicle_status")
    assert data["summary"]["outside_temperature"] == {"value": 68.0, "unit": "°F"}
    assert data["climate"]["set_temperature"] == {"value": 72.0, "unit": "°F"}
    assert data["battery"]["ev_battery_temperature_min"] == {"value": 59, "unit": "°F"}
    assert data["battery"]["ev_battery_temperature_max"] == {"value": 64, "unit": "°F"}
    assert data["charging"]["time_to_full"] == {"value": 95, "unit": "min"}
    assert data["service"] == {
        "next_service_in": {"value": 4200, "unit": "mi"},
        "last_service_at": {"value": 11000, "unit": "mi"},
    }
    assert data["schedule"]["departures"][0]["climate_temperature"]["unit"] == "°F"


def test_redact_text_shortens_vins_only():
    text = "No vehicle matches 'KMHXYZ12345678901'. Use bluelink_list_vehicles (ServiceTemporaryUnavailable)."
    assert (
        redact_text(text)
        == "No vehicle matches '…678901'. Use bluelink_list_vehicles (ServiceTemporaryUnavailable)."
    )
    assert redact_text("abcdefghijklmnopq") == "abcdefghijklmnopq"


def test_vehicle_errors_do_not_repeat_a_full_vin(tmp_path):
    server, _ = make(tmp_path)
    with pytest.raises(ToolError) as info:
        call(server, "bluelink_get_vehicle_status", {"vehicle": "KMHXYZ12345678901"})
    assert "KMHXYZ12345678901" not in str(info.value)
    assert "…678901" in str(info.value)


def test_audit_log_records_unlocks_refused_before_asking(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "can_elicit", lambda ctx: True)
    log = tmp_path / "audit.jsonl"
    server, _ = make(
        tmp_path, BLUELINK_ALLOW_WRITES="true", BLUELINK_ALLOW_UNLOCK="true", BLUELINK_AUDIT_LOG=str(log)
    )
    with pytest.raises(ToolError):
        call(server, "bluelink_unlock_vehicle", {"vehicle": "KMHXYZ12345678901"})
    [entry] = audit_lines(log)
    assert entry["tool"] == "bluelink_unlock_vehicle"
    assert entry["outcome"] == "error"
    assert entry["arguments"] == {"vehicle": "…678901"}
    assert "No vehicle matches" in entry["error"]
    assert "KMHXYZ12345678901" not in log.read_text()
    assert FakeManager.instances[-1].calls.count(("unlock", "v1")) == 0


def test_status_is_formatted_under_the_lock(tmp_path):
    config = Config.from_env({**ENV, "BLUELINK_TOKEN_FILE": str(tmp_path / "tokens.json")})
    client = BluelinkClient(config, manager_factory=FakeManager)
    assert client.status(None, lambda v: client._lock._is_owned())
    assert client.force_refresh(None, lambda v: client._lock._is_owned())
    assert all(client.vehicles(lambda v: client._lock._is_owned()))


@pytest.mark.parametrize("action_id", ["x" * 129, "act 1", "act;rm", ""])
def test_action_id_is_validated(tmp_path, action_id):
    server, _ = make(tmp_path)
    with pytest.raises(ToolError):
        call(server, "bluelink_get_command_status", {"action_id": action_id})


def test_action_id_accepts_real_looking_ids(tmp_path):
    server, _ = make(tmp_path)
    data, _ = call(
        server, "bluelink_get_command_status", {"action_id": "3f2b1c9e-7a4d-4e0f-9b8a-1c2d3e4f5a6b"}
    )
    assert data["status"] == "success"


class TripManager(FakeManager):
    def update_day_trip_info(self, vid, day):
        self.calls.append(("day_trips", vid, day))
        self.vehicles[vid].day_trip_info = {"day": day}

    def update_month_trip_info(self, vid, month):
        self.calls.append(("month_trips", vid, month))
        self.vehicles[vid].month_trip_info = {"month": month}


def test_trips_default_to_today_in_the_cars_time_zone(tmp_path):
    zone = dt.timezone(dt.timedelta(hours=14))

    class FarEastManager(TripManager):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.vehicles["v1"].timezone = zone

    server, _ = make(tmp_path, factory=FarEastManager)
    data, _ = call(server, "bluelink_get_trips")
    today = dt.datetime.now(zone).date()
    assert data["date"] == today.isoformat()
    assert ("day_trips", "v1", today.strftime("%Y%m%d")) in FakeManager.instances[-1].calls


def test_trips_for_a_day_and_a_month(tmp_path):
    server, _ = make(tmp_path, factory=TripManager)
    assert call(server, "bluelink_get_trips", {"date": "2026-10-15"})[0]["date"] == "2026-10-15"
    assert call(server, "bluelink_get_trips", {"date": "2026-10"})[0]["date"] == "2026-10"
    calls = FakeManager.instances[-1].calls
    assert ("day_trips", "v1", "20261015") in calls and ("month_trips", "v1", "202610") in calls
    with pytest.raises(ToolError, match="YYYY-MM-DD"):
        call(server, "bluelink_get_trips", {"date": "15/10/2026"})


# --- horn and lights ---------------------------------------------------------


class HornManager(FakeManager):
    def start_hazard_lights(self, vid):
        self.calls.append(("lights", vid))
        return "act-lights"

    def start_hazard_lights_and_horn(self, vid):
        self.calls.append(("horn", vid))
        return "act-horn"


def test_horn_and_lights_are_write_tools(tmp_path):
    server, _ = make(tmp_path, factory=HornManager)
    assert not {"bluelink_flash_hazard_lights", "bluelink_sound_horn_and_flash_lights"} & tool_names(server)
    server, _ = make(tmp_path, factory=HornManager, BLUELINK_ALLOW_WRITES="true")
    tools = {t.name: t for t in anyio.run(server.list_tools)}
    assert tools["bluelink_sound_horn_and_flash_lights"].annotations.read_only_hint is False
    assert tools["bluelink_sound_horn_and_flash_lights"].annotations.destructive_hint is False


def test_sound_horn_is_sent_and_rate_limited(tmp_path):
    log = tmp_path / "audit.jsonl"
    server, _ = make(tmp_path, factory=HornManager, BLUELINK_ALLOW_WRITES="true", BLUELINK_AUDIT_LOG=str(log))
    data, _ = call(server, "bluelink_sound_horn_and_flash_lights")
    assert data == {"vehicle": "Santa Fe", "action_id": "act-horn", "status": "success"}
    with pytest.raises(ToolError, match="bluelink_sound_horn_and_flash_lights was sent"):
        call(server, "bluelink_sound_horn_and_flash_lights")
    # Its own rate limit: the lights can still be flashed.
    call(server, "bluelink_flash_hazard_lights", {"wait": False})
    calls = FakeManager.instances[-1].calls
    assert calls.count(("horn", "v1")) == 1 and calls.count(("lights", "v1")) == 1
    assert [e["tool"] for e in audit_lines(log)] == [
        "bluelink_sound_horn_and_flash_lights",
        "bluelink_sound_horn_and_flash_lights",
        "bluelink_flash_hazard_lights",
    ]


class NoExtrasApi(ApiImpl):
    """A region API that implements none of the optional commands."""


class NoExtrasManager(HornManager):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.api = NoExtrasApi()


@pytest.mark.parametrize("tool", ["bluelink_flash_hazard_lights", "bluelink_sound_horn_and_flash_lights"])
def test_horn_and_lights_are_refused_where_the_region_lacks_them(tmp_path, tool):
    server, _ = make(tmp_path, factory=NoExtrasManager, BLUELINK_ALLOW_WRITES="true")
    for _ in range(2):  # refused before sending, so not rate limited either
        with pytest.raises(ToolError, match="isn't available in this Bluelink region"):
            call(server, tool)
    assert not {c[0] for c in FakeManager.instances[-1].calls} & {"horn", "lights"}


def test_library_not_implemented_is_explained_and_not_rate_limited(tmp_path):
    class GatedManager(HornManager):
        def start_hazard_lights_and_horn(self, vid):
            raise NotImplementedError("awaits live verification")

    server, _ = make(tmp_path, factory=GatedManager, BLUELINK_ALLOW_WRITES="true")
    for _ in range(2):
        with pytest.raises(ToolError, match="isn't available for this car's region or brand"):
            call(server, "bluelink_sound_horn_and_flash_lights")


# --- schedules ---------------------------------------------------------------


class ScheduleManager(FakeManager):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        car = self.vehicles["v1"]
        car.ev_schedule_charge_enabled = False
        car.ev_off_peak_start_time = dt.time(0, 30)
        car.ev_off_peak_end_time = dt.time(7, 30)
        car.ev_off_peak_charge_only_enabled = False
        for label, hour in (("first", 8), ("second", 17)):
            setattr(car, f"ev_{label}_departure_enabled", True)
            setattr(car, f"ev_{label}_departure_days", [1, 2, 3, 4, 5])
            setattr(car, f"ev_{label}_departure_time", dt.time(hour, 0))
            setattr(car, f"ev_{label}_departure_climate_enabled", True)
            setattr(car, f"ev_{label}_departure_climate_temperature", (21.0, "°C"))
            setattr(car, f"ev_{label}_departure_climate_defrost", False)

    def schedule_charging_and_climate(self, vid, options):
        self.calls.append(("schedule", vid, options))
        return "act-schedule"

    def schedule_reservation_charge(self, vid, options):
        self.calls.append(("reservation_charge", vid, options))
        return "act-rc"

    def schedule_reservation_hvac(self, vid, options):
        self.calls.append(("reservation_hvac", vid, options))
        return "act-rh"


def sent_schedule(kind="schedule"):
    [options] = [c[2] for c in FakeManager.instances[-1].calls if c[0] == kind]
    return options


def test_schedule_tools_need_writes(tmp_path):
    server, _ = make(tmp_path, factory=ScheduleManager)
    assert not {"bluelink_set_charging_schedule", "bluelink_set_departure_schedule"} & tool_names(server)


def test_charging_schedule_keeps_what_is_not_changed(tmp_path):
    server, _ = make(tmp_path, factory=ScheduleManager, BLUELINK_ALLOW_WRITES="true")
    data, _ = call(
        server, "bluelink_set_charging_schedule", {"scheduled_charging": True, "off_peak_start": "23:15"}
    )
    assert data["action_id"] == "act-schedule" and data["status"] == "success"
    assert data["schedule"] == {
        "scheduled_charging": True,
        "off_peak_start": "23:15",
        "off_peak_end": "07:30",
        "off_peak_only": False,
    }
    options = sent_schedule()
    assert (options.charging_enabled, options.off_peak_start_time) == (True, dt.time(23, 15))
    assert options.off_peak_end_time == dt.time(7, 30)
    # Nothing is left for the library to fill with defaults.
    assert options.first_departure.time == dt.time(8, 0)
    assert options.second_departure.time == dt.time(17, 0)
    assert options.first_departure.days == [1, 2, 3, 4, 5]
    assert options.temperature == 21.0
    # The car's state was read just before filling in the schedule.
    calls = FakeManager.instances[-1].calls
    assert calls.index(("cached", "v1")) < [c[0] for c in calls].index("schedule")


def test_departure_schedule_changes_one_slot(tmp_path):
    server, _ = make(tmp_path, factory=ScheduleManager, BLUELINK_ALLOW_WRITES="true")
    data, _ = call(
        server,
        "bluelink_set_departure_schedule",
        {"slot": 2, "days": ["Sat", "Sun", "Sat"], "time": "09:45", "temperature": 22.3, "wait": False},
    )
    assert data["departure"] == {
        "slot": 2,
        "enabled": True,
        "days": ["Sun", "Sat"],
        "time": "09:45",
        "climate": True,
        "temperature": {"value": 22.5, "unit": "°C"},
        "defrost": False,
    }
    options = sent_schedule()
    assert options.second_departure.days == [0, 6]
    assert options.second_departure.time == dt.time(9, 45)
    assert options.first_departure.time == dt.time(8, 0)
    assert options.first_departure.temperature == 21.0
    # Where the climate setting is shared, it follows the departure that changed.
    assert options.temperature == 22.5
    assert options.charging_enabled is False and options.off_peak_start_time == dt.time(0, 30)


def test_departure_temperature_is_converted_for_fahrenheit_cars(tmp_path):
    class FahrenheitManager(ScheduleManager):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.vehicles["v1"].ev_first_departure_climate_temperature = (70, "°F")

    server, _ = make(tmp_path, factory=FahrenheitManager, BLUELINK_ALLOW_WRITES="true")
    data, _ = call(server, "bluelink_set_departure_schedule", {"slot": 1, "temperature": 21})
    assert data["departure"]["temperature"] == {"value": 70, "unit": "°F"}
    assert sent_schedule().temperature_unit == 1


def test_schedule_is_refused_when_the_car_does_not_report_it(tmp_path):
    class UnknownManager(ScheduleManager):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.vehicles["v1"].ev_off_peak_end_time = None
            self.vehicles["v1"].ev_second_departure_time = None

    server, _ = make(tmp_path, factory=UnknownManager, BLUELINK_ALLOW_WRITES="true")
    for _ in range(2):  # refused before sending, so not rate limited
        with pytest.raises(ToolError, match="off_peak_end, departure 2 time"):
            call(server, "bluelink_set_charging_schedule", {"scheduled_charging": True})
    assert not [c for c in FakeManager.instances[-1].calls if c[0] == "schedule"]
    # Giving the missing value explicitly is enough for that one.
    with pytest.raises(ToolError, match="departure 2 time"):
        call(server, "bluelink_set_charging_schedule", {"off_peak_end": "06:00"})


def test_schedule_needs_a_change(tmp_path):
    server, _ = make(tmp_path, factory=ScheduleManager, BLUELINK_ALLOW_WRITES="true")
    with pytest.raises(ToolError, match="Nothing to change"):
        call(server, "bluelink_set_charging_schedule")
    with pytest.raises(ToolError, match="Nothing to change"):
        call(server, "bluelink_set_departure_schedule", {"slot": 1})


@pytest.mark.parametrize("bad", ["24:00", "7:30", "07:60", "noon"])
def test_schedule_times_are_validated(tmp_path, bad):
    server, _ = make(tmp_path, factory=ScheduleManager, BLUELINK_ALLOW_WRITES="true")
    with pytest.raises(ToolError):
        call(server, "bluelink_set_charging_schedule", {"off_peak_start": bad})


class KiaCciLikeApi(ApiImpl):
    """Like the library's Kia europe-cci API: only some endpoints are enabled."""

    GSPA_REMOTE_CONTROL_VERIFIED = False
    GSPA_VERIFIED_ENDPOINTS = frozenset({"reservation-charge", "reservation-hvac"})

    def schedule_charging_and_climate(self, *args): ...

    def schedule_reservation_charge(self, *args): ...

    def schedule_reservation_hvac(self, *args): ...


class HyundaiCciLikeApi(KiaCciLikeApi):
    GSPA_REMOTE_CONTROL_VERIFIED = True


@pytest.mark.parametrize(
    ("api", "tool", "args", "kind"),
    [
        (KiaCciLikeApi, "bluelink_set_charging_schedule", {"scheduled_charging": True}, "reservation_charge"),
        (KiaCciLikeApi, "bluelink_set_departure_schedule", {"slot": 1, "enabled": False}, "reservation_hvac"),
        (HyundaiCciLikeApi, "bluelink_set_charging_schedule", {"scheduled_charging": True}, "schedule"),
        (HyundaiCciLikeApi, "bluelink_set_departure_schedule", {"slot": 1, "enabled": False}, "schedule"),
    ],
)
def test_europe_cci_schedules_use_the_enabled_endpoint(tmp_path, api, tool, args, kind):
    class CciManager(ScheduleManager):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.api = api()

    server, _ = make(tmp_path, factory=CciManager, BLUELINK_ALLOW_WRITES="true")
    call(server, tool, {**args, "wait": False})
    assert [c[0] for c in FakeManager.instances[-1].calls if c[0] not in ("login", "cached")] == [kind]
    options = sent_schedule(kind)
    if kind == "reservation_charge":
        assert options.charging_enabled is True and options.first_departure is None
    if kind == "reservation_hvac":
        assert options.first_departure.enabled is False and options.charging_enabled is None


def test_schedules_are_refused_where_the_region_lacks_them(tmp_path):
    class NoScheduleManager(ScheduleManager):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.api = NoExtrasApi()

    server, _ = make(tmp_path, factory=NoScheduleManager, BLUELINK_ALLOW_WRITES="true")
    with pytest.raises(ToolError, match="isn't available in this Bluelink region"):
        call(server, "bluelink_set_charging_schedule", {"scheduled_charging": True})


# --- charge port and charging current ----------------------------------------


class PortManager(FakeManager):
    def open_charge_port(self, vid):
        self.calls.append(("port_open", vid))
        return "act-po"

    def close_charge_port(self, vid):
        self.calls.append(("port_close", vid))
        return "act-pc"

    def set_charging_current(self, vid, level):
        self.calls.append(("current", vid, level))
        return "act-cur"


def test_charge_port_and_current_need_writes(tmp_path):
    server, _ = make(tmp_path, factory=PortManager)
    assert not {
        "bluelink_open_charge_port",
        "bluelink_close_charge_port",
        "bluelink_set_charging_current",
    } & tool_names(server)


def test_charge_port_open_and_close(tmp_path):
    server, _ = make(tmp_path, factory=PortManager, BLUELINK_ALLOW_WRITES="true")
    assert call(server, "bluelink_open_charge_port")[0]["action_id"] == "act-po"
    assert call(server, "bluelink_close_charge_port")[0]["action_id"] == "act-pc"
    with pytest.raises(ToolError, match="bluelink_open_charge_port was sent"):
        call(server, "bluelink_open_charge_port")
    calls = FakeManager.instances[-1].calls
    assert calls.count(("port_open", "v1")) == 1 and calls.count(("port_close", "v1")) == 1


@pytest.mark.parametrize(("percent", "level"), [(100, 1), (90, 2), (60, 3)])
def test_set_charging_current(tmp_path, percent, level):
    server, _ = make(tmp_path, factory=PortManager, BLUELINK_ALLOW_WRITES="true")
    call(server, "bluelink_set_charging_current", {"percent": percent, "wait": False})
    assert ("current", "v1", level) in FakeManager.instances[-1].calls


@pytest.mark.parametrize("percent", [50, 0, 110, "100"])
def test_set_charging_current_takes_only_the_library_levels(tmp_path, percent):
    server, _ = make(tmp_path, factory=PortManager, BLUELINK_ALLOW_WRITES="true")
    with pytest.raises(ToolError):
        call(server, "bluelink_set_charging_current", {"percent": percent})
    assert not [c for m in FakeManager.instances for c in m.calls if c[0] == "current"]


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("bluelink_open_charge_port", {}),
        ("bluelink_close_charge_port", {}),
        ("bluelink_set_charging_current", {"percent": 60}),
    ],
)
def test_charge_port_and_current_are_refused_where_the_region_lacks_them(tmp_path, tool, args):
    class NoPortManager(PortManager):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.api = NoExtrasApi()

    server, _ = make(tmp_path, factory=NoPortManager, BLUELINK_ALLOW_WRITES="true")
    with pytest.raises(ToolError, match="isn't available in this Bluelink region"):
        call(server, tool, args)
    assert [c[0] for c in FakeManager.instances[-1].calls] == ["login"]


def test_status_shows_the_charging_current_as_a_percentage(tmp_path):
    class CurrentManager(FakeManager):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.vehicles["v1"].ev_charging_current = 2

    server, _ = make(tmp_path, factory=CurrentManager)
    assert call(server, "bluelink_get_vehicle_status")[0]["charging"]["charging_current_percent"] == 90


# --- battery conditioning ----------------------------------------------------


class ConditioningManager(FakeManager):
    def start_battery_conditioning(self, vid):
        self.calls.append(("conditioning_start", vid))
        return "act-bcs"

    def stop_battery_conditioning(self, vid):
        self.calls.append(("conditioning_stop", vid))
        return "act-bce"


def test_battery_conditioning_needs_writes(tmp_path):
    server, _ = make(tmp_path, factory=ConditioningManager)
    assert not {"bluelink_start_battery_conditioning", "bluelink_stop_battery_conditioning"} & tool_names(
        server
    )


def test_battery_conditioning_start_and_stop(tmp_path):
    log = tmp_path / "audit.jsonl"
    server, _ = make(
        tmp_path, factory=ConditioningManager, BLUELINK_ALLOW_WRITES="true", BLUELINK_AUDIT_LOG=str(log)
    )
    assert call(server, "bluelink_start_battery_conditioning")[0]["action_id"] == "act-bcs"
    with pytest.raises(ToolError, match="bluelink_start_battery_conditioning was sent"):
        call(server, "bluelink_start_battery_conditioning")
    assert call(server, "bluelink_stop_battery_conditioning")[0]["action_id"] == "act-bce"
    calls = FakeManager.instances[-1].calls
    assert calls.count(("conditioning_start", "v1")) == 1 and calls.count(("conditioning_stop", "v1")) == 1
    assert [e["outcome"] for e in audit_lines(log)] == ["ok", "error", "ok"]


@pytest.mark.parametrize(
    "tool", ["bluelink_start_battery_conditioning", "bluelink_stop_battery_conditioning"]
)
def test_battery_conditioning_is_refused_outside_europe_cci(tmp_path, tool):
    class OldRegionManager(ConditioningManager):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.api = NoExtrasApi()

    server, _ = make(tmp_path, factory=OldRegionManager, BLUELINK_ALLOW_WRITES="true")
    with pytest.raises(ToolError, match="isn't available in this Bluelink region"):
        call(server, tool)
    assert [c[0] for c in FakeManager.instances[-1].calls] == ["login"]


def test_battery_conditioning_is_only_in_europe_cci_in_the_library():
    from hyundai_kia_connect_api import VehicleManager

    from bluelink_mcp.client import region_supports

    def supports(region, brand):
        api = VehicleManager.get_implementation_by_region_brand(region, brand, "en")
        return region_supports(type("M", (), {"api": api})(), "start_battery_conditioning")

    assert supports(9, 2)  # europe-cci, Hyundai
    assert not supports(1, 2)  # europe, Hyundai
    assert not supports(2, 2)  # Canada


# --- windows -----------------------------------------------------------------


class WindowManager(FakeManager):
    def set_windows_state(self, vid, options):
        self.calls.append(("windows", vid, options))
        return "act-win"


def test_close_windows_needs_writes_and_there_is_no_open(tmp_path):
    server, _ = make(tmp_path, factory=WindowManager)
    assert "bluelink_close_windows" not in tool_names(server)
    server, _ = make(
        tmp_path, factory=WindowManager, BLUELINK_ALLOW_WRITES="true", BLUELINK_ALLOW_UNLOCK="true"
    )
    names = tool_names(server)
    assert "bluelink_close_windows" in names
    assert not [n for n in names if "window" in n and n != "bluelink_close_windows"]


def test_close_windows_closes_all_four(tmp_path):
    from hyundai_kia_connect_api.const import WINDOW_STATE

    server, _ = make(tmp_path, factory=WindowManager, BLUELINK_ALLOW_WRITES="true")
    data, _ = call(server, "bluelink_close_windows")
    assert data["action_id"] == "act-win"
    [options] = [c[2] for c in FakeManager.instances[-1].calls if c[0] == "windows"]
    sides = (options.front_left, options.front_right, options.back_left, options.back_right)
    assert sides == (WINDOW_STATE.CLOSED,) * 4
    assert options.rear_left_curtain is None and options.rear_right_curtain is None
    with pytest.raises(ToolError, match="bluelink_close_windows was sent"):
        call(server, "bluelink_close_windows")


def test_close_windows_is_refused_where_the_region_lacks_it(tmp_path):
    class NoWindowManager(WindowManager):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.api = NoExtrasApi()

    server, _ = make(tmp_path, factory=NoWindowManager, BLUELINK_ALLOW_WRITES="true")
    with pytest.raises(ToolError, match="isn't available in this Bluelink region"):
        call(server, "bluelink_close_windows")
