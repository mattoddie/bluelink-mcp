import datetime as dt
import json
import os
import stat
import threading

import anyio
import pytest
from hyundai_kia_connect_api import Vehicle
from hyundai_kia_connect_api.const import ORDER_STATUS
from hyundai_kia_connect_api.exceptions import APIError, AuthenticationError
from hyundai_kia_connect_api.Token import Token
from mcp.server.mcpserver.exceptions import ToolError

from bluelink_mcp.__main__ import transport_security
from bluelink_mcp.client import BluelinkClient, check_supported
from bluelink_mcp.config import Config, ConfigError
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
    env = {**ENV, "BLUELINK_TOKEN_FILE": str(tmp_path / "token.json"), **extra}
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


def test_read_only_server_has_no_command_tools(tmp_path):
    server, _ = make(tmp_path)
    names = tool_names(server)
    assert "get_vehicle_status" in names
    assert not names & {"lock_vehicle", "unlock_vehicle", "start_climate", "start_charging"}


def test_unlock_needs_its_own_switch(tmp_path):
    server, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true")
    assert "lock_vehicle" in tool_names(server)
    assert "unlock_vehicle" not in tool_names(server)
    server, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true", BLUELINK_ALLOW_UNLOCK="true")
    assert "unlock_vehicle" in tool_names(server)


def test_status_and_token_cache(tmp_path):
    server, config = make(tmp_path)
    data, _ = call(server, "get_vehicle_status")
    assert data["battery"]["ev_battery_percent"] == 80
    assert data["summary"]["locked"] is True
    # Unknown fields are dropped rather than returned as nulls.
    assert "tyres" not in data

    saved = json.loads(config.token_file.read_text())
    assert saved["access_token"] == "abc"
    assert stat.S_IMODE(os.stat(config.token_file).st_mode) == 0o600

    # A second server reuses the cached token instead of logging in again.
    server2, _ = make(tmp_path)
    call(server2, "list_vehicles")
    assert ("login",) not in FakeManager.instances[-1].calls


def test_status_is_cached_briefly(tmp_path):
    server, _ = make(tmp_path)
    call(server, "get_vehicle_status")
    call(server, "get_vehicle_location")
    assert FakeManager.instances[-1].calls.count(("cached", "v1")) == 1


def test_force_refresh_is_rate_limited(tmp_path):
    server, _ = make(tmp_path)
    call(server, "refresh_vehicle_status")
    with pytest.raises(ToolError, match="12V battery"):
        call(server, "refresh_vehicle_status")
    assert FakeManager.instances[-1].calls.count(("force", "v1")) == 1


def test_location(tmp_path):
    server, _ = make(tmp_path)
    data, _ = call(server, "get_vehicle_location")
    assert (data["latitude"], data["longitude"]) == (51.5, -0.12)
    assert "openstreetmap.org" in data["map"]


def test_lock_waits_for_confirmation(tmp_path):
    server, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true")
    data, _ = call(server, "lock_vehicle", {"vehicle": "santa"})
    assert data == {"vehicle": "Santa Fe", "action_id": "act-1", "status": "success"}


def test_start_climate_options(tmp_path):
    server, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true")
    call(
        server,
        "start_climate",
        {"temperature": 21.3, "heated_steering_wheel": True, "front_left_seat": "high_heat"},
    )
    _, _, options = next(c for c in FakeManager.instances[-1].calls if c[0] == "climate")
    assert options.set_temp == 21.5
    assert options.heating == 3
    assert options.steering_wheel == 1
    assert options.front_left_seat == 8
    assert options.front_right_seat is None

    with pytest.raises(ToolError, match="between 14 and 30"):
        call(server, "start_climate", {"temperature": 40})


def test_unknown_vehicle_message(tmp_path):
    server, _ = make(tmp_path)
    with pytest.raises(ToolError, match="No vehicle matches"):
        call(server, "get_vehicle_status", {"vehicle": "ioniq"})


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
    call(server, "list_vehicles")
    token = FakeManager.instances[-1].kwargs["token"]
    assert (token.access_token, token.password, token.pin) == ("cached", "secret", "1234")
    # The cache is rewritten with the current credentials.
    saved = json.loads(config.token_file.read_text())
    assert (saved["password"], saved["pin"]) == ("secret", "1234")


class RejectingManager(FakeManager):
    def check_and_refresh_token(self):
        self.calls.append(("login",))
        raise AuthenticationError("bad password, response body: s3cr3t-fragment")


def test_failed_login_discards_cache_and_backs_off(tmp_path):
    server, config = make(tmp_path, factory=RejectingManager)
    write_cache(config)
    with pytest.raises(ToolError, match="rejected the login") as err:
        call(server, "list_vehicles")
    assert "s3cr3t-fragment" not in str(err.value)
    assert "AuthenticationError" in str(err.value)
    assert not config.token_file.exists()

    # No new login attempt until the backoff is over.
    with pytest.raises(ToolError, match="waits before trying again"):
        call(server, "list_vehicles")
    assert len(FakeManager.instances) == 1


def test_login_backoff_can_be_turned_off(tmp_path):
    server, _ = make(tmp_path, factory=RejectingManager, BLUELINK_LOGIN_RETRY_MINUTES="0")
    for _ in range(2):
        with pytest.raises(ToolError, match="rejected the login"):
            call(server, "list_vehicles")
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
    call(server, "list_vehicles")
    assert FakeManager.instances[-1].kwargs["token"] is None
    assert ("login",) in FakeManager.instances[-1].calls


def test_manager_construction_errors_are_wrapped(tmp_path):
    def broken(**kwargs):
        raise APIError("Unknown brand, raw details")

    server, _ = make(tmp_path, factory=broken)
    with pytest.raises(ToolError, match="Could not log in.*APIError"):
        call(server, "list_vehicles")


def test_token_cache_leaves_no_temporary_files(tmp_path):
    server, config = make(tmp_path)
    call(server, "list_vehicles")
    assert [p.name for p in tmp_path.iterdir()] == ["token.json"]
    assert stat.S_IMODE(os.stat(config.token_file).st_mode) == 0o600


def test_unsupported_region_and_brand_is_a_config_error():
    check_supported(Config.from_env(ENV))
    with pytest.raises(ConfigError, match="genesis is not supported in BLUELINK_REGION=europe-cci"):
        check_supported(
            Config.from_env({**ENV, "BLUELINK_REGION": "europe-cci", "BLUELINK_BRAND": "genesis"})
        )


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
        call(server, "get_vehicle_status")
    assert str(err.value).endswith("Bluelink request failed: Bluelink returned an error. (APIError)")
    assert "12345" not in str(err.value)


def test_failed_force_refresh_is_rate_limited_too(tmp_path):
    server, _ = make(tmp_path, factory=FailingManager)
    with pytest.raises(ToolError, match="APIError"):
        call(server, "refresh_vehicle_status")
    with pytest.raises(ToolError, match="12V battery"):
        call(server, "refresh_vehicle_status")
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
            results["lock"] = (await server.call_tool("lock_vehicle", {})).structured_content

        async with anyio.create_task_group() as tg:
            tg.start_soon(lock)
            while not FakeManager.instances or ("lock", "v1") not in FakeManager.instances[-1].calls:
                await anyio.sleep(0.01)
            with anyio.fail_after(5):
                results["status"] = (await server.call_tool("get_vehicle_status", {})).structured_content

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
    data, _ = call(server, "lock_vehicle")
    assert data == {"vehicle": "Santa Fe", "action_id": "act-1", "status": "pending"}


def test_start_climate_in_usa_sends_fahrenheit(tmp_path):
    server, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true", BLUELINK_REGION="usa")
    sent = []
    for celsius in (21, 14, 30):
        data, _ = call(server, "start_climate", {"temperature": celsius})
        sent.append(data["temperature_sent"])
    temps = [c[2].set_temp for c in FakeManager.instances[-1].calls if c[0] == "climate"]
    assert temps == [70, 62, 82]
    assert sent == ["70 °F", "62 °F", "82 °F"]


def test_start_climate_in_canada_respects_older_cars(tmp_path):
    server, _ = make(tmp_path, BLUELINK_ALLOW_WRITES="true", BLUELINK_REGION="canada")
    call(server, "list_vehicles")
    FakeManager.instances[-1].vehicles["v1"].year = 2019
    with pytest.raises(ToolError, match="lowest temperature is 16"):
        call(server, "start_climate", {"temperature": 15})
    call(server, "start_climate", {"temperature": 16})
    _, _, options = next(c for c in FakeManager.instances[-1].calls if c[0] == "climate")
    assert options.set_temp == 16


# --- vehicles and formatting -----------------------------------------------


class TwoCarManager(FakeManager):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.vehicles["v2"] = Vehicle(id="v2", name="Ioniq 5", model="IONIQ 5", year=2023, VIN="KMHABC")


def test_blank_vehicle_means_the_only_car(tmp_path):
    server, _ = make(tmp_path)
    data, _ = call(server, "get_vehicle_status", {"vehicle": "   "})
    assert data["vehicle"]["id"] == "v1"


def test_blank_vehicle_is_ambiguous_with_several_cars(tmp_path):
    server, _ = make(tmp_path, factory=TwoCarManager)
    with pytest.raises(ToolError, match="say which one"):
        call(server, "get_vehicle_status", {"vehicle": " "})


def test_unlock_needs_an_exact_vehicle_with_several_cars(tmp_path):
    server, _ = make(
        tmp_path, factory=TwoCarManager, BLUELINK_ALLOW_WRITES="true", BLUELINK_ALLOW_UNLOCK="true"
    )
    # Part of a name is fine for locking...
    data, _ = call(server, "lock_vehicle", {"vehicle": "santa"})
    assert data["vehicle"] == "Santa Fe"
    # ...but not for unlocking.
    with pytest.raises(ToolError, match="exact name"):
        call(server, "unlock_vehicle", {"vehicle": "santa"})
    assert not any(c[0] == "unlock" for c in FakeManager.instances[-1].calls)
    data, _ = call(server, "unlock_vehicle", {"vehicle": "santa fe"})
    assert (data["vehicle"], data["status"]) == ("Santa Fe", "success")
    data, _ = call(server, "unlock_vehicle", {"vehicle": "KMHABC"})
    assert data["vehicle"] == "Ioniq 5"


def test_fuel_range_has_its_unit(tmp_path):
    class PetrolManager(FakeManager):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.vehicles["v1"].fuel_driving_range = (420, "km")

    server, _ = make(tmp_path, factory=PetrolManager)
    data, _ = call(server, "get_vehicle_status")
    assert data["fuel"]["fuel_range"] == {"value": 420, "unit": "km"}


# --- HTTP settings ---------------------------------------------------------


def test_allowed_origins():
    assert transport_security(Config.from_env(ENV)) is None
    config = Config.from_env(
        {
            **ENV,
            "MCP_ALLOWED_HOSTS": "bluelink.example.com",
            "MCP_ALLOWED_ORIGINS": "https://claude.ai, https://bluelink.example.com",
        }
    )
    security = transport_security(config)
    assert security.allowed_hosts == ["bluelink.example.com"]
    assert security.allowed_origins == ["https://claude.ai", "https://bluelink.example.com"]
    with pytest.raises(ConfigError, match="MCP_ALLOWED_ORIGINS needs MCP_ALLOWED_HOSTS"):
        Config.from_env({**ENV, "MCP_ALLOWED_ORIGINS": "https://claude.ai"})
