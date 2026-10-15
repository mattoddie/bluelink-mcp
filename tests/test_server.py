import datetime as dt
import json
import os
import stat

import anyio
import pytest
from hyundai_kia_connect_api import Vehicle
from hyundai_kia_connect_api.const import ORDER_STATUS
from hyundai_kia_connect_api.Token import Token
from mcp.server.mcpserver.exceptions import ToolError

from bluelink_mcp.client import BluelinkClient
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

    def start_climate(self, vid, options):
        self.calls.append(("climate", vid, options))
        return "act-2"

    def check_action_status(self, vid, action_id, synchronous=False, timeout=120):
        return ORDER_STATUS.SUCCESS


ENV = {"BLUELINK_USERNAME": "me@example.com", "BLUELINK_PASSWORD": "secret", "BLUELINK_PIN": "1234"}


def make(tmp_path, **extra):
    FakeManager.instances.clear()
    env = {**ENV, "BLUELINK_TOKEN_FILE": str(tmp_path / "token.json"), **extra}
    config = Config.from_env(env)
    client = BluelinkClient(config, manager_factory=FakeManager)
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
